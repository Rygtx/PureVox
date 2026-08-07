// PureVox — AI 麦克风降噪工具
// Copyright (C) 2024-2026 a2heng <752848283@qq.com>
//
// PureVox is licensed under the GNU General Public License v3.0 or
// later (GPL-3.0-or-later).  See LICENSE for details.
// 
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
// 
// The built-in AI models are NOT covered by the GPL; they are the
// property of a2heng and may only be used with PureVox under
// authorization.  See MODEL-LICENSE.md for details.
// 
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pvpipe — PureVox 原生 PipeWire 桥接（pybind11 扩展，仅 Linux）。
//
// 为什么用原生 PipeWire（取代旧 GStreamer / JACK）：
//   - 格式协商时直接声明 F32 单声道 48000Hz，PipeWire 内置重采样 + 声道
//     转换，模型永远拿 48k 单声道，输出自动上混到目标设备声道数——
//     不存在"一个通道一个模型/通道不匹配/采样率不齐"。
//   - 无 JACK 依赖（libjack/jackdbus-detect），更现代。
//
// 结构（PwBridge）：
//   - input 流   （Stream/Input/Audio，PW_KEY_TARGET_OBJECT=源节点名）
//   - output 流  （Stream/Output/Audio，目标=输出节点名）
//   - monitor 流 （可选，目标=监听节点名；同一路降噪音频）
//   进程回调（实时/主循环线程）只做无锁 SPSC 环形缓冲搬运，Python 线程
//   读取→降噪→写入。
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <pipewire/pipewire.h>
#include <spa/param/audio/format-utils.h>
#include <spa/param/audio/raw-utils.h>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

// ── 无锁 SPSC 环形缓冲（进程回调实时安全，禁锁/禁分配）──────────────
class SPSCRing {
public:
    explicit SPSCRing(size_t capacity) {
        size_t cap = 1;
        while (cap < capacity) cap <<= 1;
        cap_ = cap;
        mask_ = cap - 1;
        buf_.resize(cap, 0.0f);
    }

    size_t available() const {
        return w_.load(std::memory_order_acquire) - r_.load(std::memory_order_acquire);
    }

    void write(const float* data, size_t n) {
        size_t w = w_.load(std::memory_order_relaxed);
        size_t r = r_.load(std::memory_order_acquire);
        size_t free = cap_ - (w - r);
        if (free < n) {
            size_t drop = n - free;
            r += drop;
            r_.store(r, std::memory_order_release);
        }
        for (size_t i = 0; i < n; ++i) buf_[(w + i) & mask_] = data[i];
        w_.store(w + n, std::memory_order_release);
    }

    void write_drop_new(const float* data, size_t n) {
        size_t w = w_.load(std::memory_order_relaxed);
        size_t r = r_.load(std::memory_order_acquire);
        size_t free = cap_ - (w - r);
        if (free < n) {
            if (free == 0) return;
            n = free;
        }
        for (size_t i = 0; i < n; ++i) buf_[(w + i) & mask_] = data[i];
        w_.store(w + n, std::memory_order_release);
    }

    size_t read(float* out, size_t n) {
        size_t w = w_.load(std::memory_order_acquire);
        size_t r = r_.load(std::memory_order_relaxed);
        size_t avail = w - r;
        if (avail < n) n = avail;
        for (size_t i = 0; i < n; ++i) out[i] = buf_[(r + i) & mask_];
        r_.store(r + n, std::memory_order_release);
        return n;
    }

    size_t read_or_silence(float* out, size_t n) {
        size_t got = read(out, n);
        for (size_t i = got; i < n; ++i) out[i] = 0.0f;
        return got;
    }

private:
    size_t cap_ = 0;
    size_t mask_ = 0;
    std::vector<float> buf_;
    std::atomic<size_t> w_{0};
    std::atomic<size_t> r_{0};
};

// ── PwBridge ──────────────────────────────────────────────────────────

static const size_t RING_CAPACITY = 96000;  // 2s @48kHz

class PwBridge {
public:
    PwBridge() : in_ring_(RING_CAPACITY), out_ring_(RING_CAPACITY), mon_ring_(RING_CAPACITY) {}
    ~PwBridge() { close(); }

    bool open(const std::string& input_name, const std::string& output_name,
              const std::string& monitor_name) {
        if (loop_) return true;
        last_error_.clear();
        pw_init(nullptr, nullptr);
        loop_ = pw_main_loop_new(nullptr);
        if (!loop_) { last_error_ = "pw_main_loop_new 失败"; return false; }
        context_ = pw_context_new(pw_main_loop_get_loop(loop_), nullptr, 0);
        if (!context_) { last_error_ = "pw_context_new 失败"; return false; }
        core_ = pw_context_connect(context_, nullptr, 0);
        if (!core_) { last_error_ = "pw_context_connect 失败（PipeWire 未运行）"; return false; }

        // 先启动主循环线程，之后所有 pw_stream 操作都经 _run_on_loop 在
        // 主循环线程执行（PipeWire 要求流操作必须在该线程调用）。
        running_ = true;
        loop_thread_ = std::thread([this]() { pw_main_loop_run(loop_); });

        bool ok = true;
        _run_on_loop([&]() {
            if (!input_name.empty()) {
                in_stream_ = _create_stream("PureVox-input", PW_DIRECTION_INPUT, input_name, &in_ring_, &in_ctx_);
                if (!in_stream_) ok = false;
            }
            if (ok && !output_name.empty()) {
                out_stream_ = _create_stream("PureVox-output", PW_DIRECTION_OUTPUT, output_name, &out_ring_, &out_ctx_);
                if (!out_stream_) ok = false;
            }
            if (ok && !monitor_name.empty()) {
                mon_stream_ = _create_stream("PureVox-monitor", PW_DIRECTION_OUTPUT, monitor_name, &mon_ring_, &mon_ctx_);
                if (!mon_stream_) ok = false;
            }
            if (ok && !in_stream_ && !out_stream_) {
                last_error_ = "未配置任何输入/输出目标";
                ok = false;
            }
        });
        if (!ok) {
            close();
            return false;
        }
        return true;
    }

    void close() {
        if (loop_thread_.joinable()) {
            // 先经主循环线程销毁流（running_ 此时仍为 true）
            _run_on_loop([&]() {
                if (in_stream_) { pw_stream_destroy(in_stream_); in_stream_ = nullptr; }
                if (out_stream_) { pw_stream_destroy(out_stream_); out_stream_ = nullptr; }
                if (mon_stream_) { pw_stream_destroy(mon_stream_); mon_stream_ = nullptr; }
            });
            running_ = false;
            pw_main_loop_quit(loop_);
            loop_thread_.join();
        }
        if (in_ctx_) { delete in_ctx_; in_ctx_ = nullptr; }
        if (out_ctx_) { delete out_ctx_; out_ctx_ = nullptr; }
        if (mon_ctx_) { delete mon_ctx_; mon_ctx_ = nullptr; }
        if (core_) { pw_core_disconnect(core_); core_ = nullptr; }
        if (context_) { pw_context_destroy(context_); context_ = nullptr; }
        if (loop_) { pw_main_loop_destroy(loop_); loop_ = nullptr; }
        pw_deinit();
    }

    bool active() const { return loop_ != nullptr; }
    std::string last_error() const { return last_error_; }
    uint32_t sample_rate() const { return rate_; }
    uint32_t buffer_size() const { return buffer_size_; }

    bool set_monitor(const std::string& monitor_name, bool enabled) {
        // 运行时开关监听流：enabled 时确保存在，否则销毁（经主循环线程执行）
        if (!core_) return false;
        bool result = false;
        _run_on_loop([&]() {
            if (enabled && !mon_stream_) {
                mon_stream_ = _create_stream("PureVox-monitor", PW_DIRECTION_OUTPUT, monitor_name, &mon_ring_, &mon_ctx_);
                result = mon_stream_ != nullptr;
            } else if (!enabled && mon_stream_) {
                pw_stream_destroy(mon_stream_);
                mon_stream_ = nullptr;
                if (mon_ctx_) { delete mon_ctx_; mon_ctx_ = nullptr; }
                result = true;
            } else {
                result = true;
            }
        });
        return result;
    }

    // 在 PipeWire 主循环线程同步执行 fn（PipeWire 要求 pw_stream 操作在该线程）。
    // 用条件变量显式等待完成（pw_loop_invoke block 参数并不可靠，直接自己同步）。
    void _run_on_loop(std::function<void()> fn) {
        if (!loop_ || !running_.load(std::memory_order_acquire)) {
            fn();  // 主循环未运行：直接内联执行
            return;
        }
        std::mutex mtx;
        std::condition_variable cv;
        std::atomic<bool> done{false};
        LoopWork work;
        work.fn = [&]() {
            fn();
            done.store(true, std::memory_order_release);
            cv.notify_one();
        };
        int res = pw_loop_invoke(pw_main_loop_get_loop(loop_), &PwBridge::_loop_work_thunk,
                                 0, nullptr, 0, false, &work);
        if (res < 0) {
            fn();  // invoke 失败：内联执行兜底
            return;
        }
        std::unique_lock<std::mutex> lk(mtx);
        cv.wait(lk, [&] { return done.load(std::memory_order_acquire); });
    }

    py::object read(size_t n) {
        std::vector<float> out(n);
        size_t got = in_ring_.read(out.data(), n);
        if (got == 0) return py::none();
        out.resize(got);
        return py::cast(out);
    }

    void write(const std::vector<float>& data) {
        if (data.empty()) return;
        out_ring_.write_drop_new(data.data(), data.size());
        if (mon_stream_) mon_ring_.write_drop_new(data.data(), data.size());
    }

private:
    struct StreamCtx {
        PwBridge* self;
        SPSCRing* ring;    // 该流对应的环
        bool is_input;     // 输入流=读环填缓冲；输出流=从环读填缓冲
    };

    struct LoopWork {
        std::function<void()> fn;
    };

    static int _loop_work_thunk(struct spa_loop* loop, bool async, uint32_t seq,
                                const void* data, size_t size, void* user_data) {
        auto* w = static_cast<LoopWork*>(user_data);
        w->fn();
        (void)loop; (void)async; (void)seq; (void)data; (void)size;
        return 0;
    }

    pw_stream* _create_stream(const char* name, pw_direction direction,
                              const std::string& target, SPSCRing* ring,
                              StreamCtx** out_ctx) {
        pw_properties* props = pw_properties_new(
            PW_KEY_NODE_NAME, name,
            PW_KEY_NODE_DESCRIPTION, "PureVox",
            PW_KEY_MEDIA_CLASS, direction == PW_DIRECTION_INPUT
                ? "Stream/Input/Audio" : "Stream/Output/Audio",
            PW_KEY_MEDIA_TYPE, "Audio",
            PW_KEY_MEDIA_CATEGORY, "Capture",
            PW_KEY_MEDIA_ROLE, "Communication",
            PW_KEY_TARGET_OBJECT, target.c_str(),
            NULL);
        if (!props) return nullptr;

        StreamCtx* ctx = new StreamCtx();
        ctx->self = this;
        ctx->is_input = (direction == PW_DIRECTION_INPUT);
        ctx->ring = ring;
        if (out_ctx) *out_ctx = ctx;

        // events 须在流生命周期内保持有效（pw_stream 只存指针不拷贝）
        static struct pw_stream_events events = {};
        events.version = PW_VERSION_STREAM_EVENTS;
        events.process = &PwBridge::_on_process;
        events.param_changed = &PwBridge::_on_param_changed;

        pw_stream* stream = pw_stream_new_simple(
            pw_main_loop_get_loop(loop_), name, props, &events, ctx);
        if (!stream) {
            delete ctx;
            if (out_ctx) *out_ctx = nullptr;
            last_error_ = std::string("创建流失败: ") + name;
            return nullptr;
        }

        struct spa_audio_info_raw info = {};
        info.format = SPA_AUDIO_FORMAT_F32;
        info.rate = 48000;
        info.channels = 1;

        uint8_t buffer[1024];
        struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
        const struct spa_pod* params[1];
        params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat, &info);

        if (pw_stream_connect(stream, direction, PW_ID_ANY,
                              static_cast<pw_stream_flags>(
                                  PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS),
                              params, 1) < 0) {
            pw_stream_destroy(stream);
            delete ctx;
            if (out_ctx) *out_ctx = nullptr;
            last_error_ = std::string("连接流失败: ") + name + " → " + target;
            return nullptr;
        }
        return stream;
    }

    static void _on_process(void* userdata) {
        StreamCtx* ctx = static_cast<StreamCtx*>(userdata);
        PwBridge* self = ctx->self;
        pw_stream* stream = nullptr;
        if (ctx->is_input) stream = self->in_stream_;
        else stream = (ctx->ring == &self->mon_ring_) ? self->mon_stream_ : self->out_stream_;
        if (!stream) return;

        pw_buffer* b = pw_stream_dequeue_buffer(stream);
        if (!b) return;
        struct spa_buffer* buf = b->buffer;
        if (buf->datas[0].data && buf->datas[0].chunk) {
            if (ctx->is_input) {
                size_t n = buf->datas[0].chunk->size / sizeof(float);
                if (n > 0) ctx->ring->write(static_cast<const float*>(buf->datas[0].data), n);
            } else {
                size_t n = buf->datas[0].chunk->size / sizeof(float);
                if (n == 0) n = buf->datas[0].maxsize / sizeof(float);
                float* data = static_cast<float*>(buf->datas[0].data);
                ctx->ring->read_or_silence(data, n);
                buf->datas[0].chunk->size = n * sizeof(float);
            }
        }
        pw_stream_queue_buffer(stream, b);
    }

    static void _on_param_changed(void* userdata, uint32_t id, const struct spa_pod* param) {
        if (param == nullptr || id != SPA_PARAM_Format) return;
        StreamCtx* ctx = static_cast<StreamCtx*>(userdata);
        PwBridge* self = ctx->self;
        struct spa_audio_info_raw info = {};
        if (spa_format_audio_raw_parse(param, &info) < 0) return;
        // 协商出的格式：理论上就是请求的 F32 单声道 48000（PipeWire 负责转换）
        if (info.rate != 0) self->rate_ = info.rate;
    }

    pw_main_loop* loop_ = nullptr;
    pw_context* context_ = nullptr;
    pw_core* core_ = nullptr;
    pw_stream* in_stream_ = nullptr;
    pw_stream* out_stream_ = nullptr;
    pw_stream* mon_stream_ = nullptr;
    StreamCtx* in_ctx_ = nullptr;
    StreamCtx* out_ctx_ = nullptr;
    StreamCtx* mon_ctx_ = nullptr;
    std::thread loop_thread_;
    std::atomic<bool> running_{false};
    SPSCRing in_ring_;
    SPSCRing out_ring_;
    SPSCRing mon_ring_;
    uint32_t rate_ = 48000;
    uint32_t buffer_size_ = 0;
    std::string last_error_;
};

PYBIND11_MODULE(pvpipe, m) {
    m.doc() = "PureVox native PipeWire bridge (input/output/monitor streams + F32 mono 48k)";
    py::class_<PwBridge>(m, "PwBridge")
        .def(py::init<>())
        .def("open", &PwBridge::open,
             py::arg("input_name"), py::arg("output_name"), py::arg("monitor_name") = "",
             "连接 PipeWire：input=源节点名，output=输出节点名，monitor=监听节点名")
        .def("close", &PwBridge::close)
        .def("active", &PwBridge::active)
        .def("last_error", &PwBridge::last_error)
        .def("sample_rate", &PwBridge::sample_rate)
        .def("buffer_size", &PwBridge::buffer_size)
        .def("set_monitor", &PwBridge::set_monitor,
             py::arg("monitor_name"), py::arg("enabled"))
        .def("read", &PwBridge::read, py::arg("n"),
             "从输入环读最多 n 个样本；无数据返回 None")
        .def("write", &PwBridge::write, py::arg("data"),
             "写样本到输出环（满则丢新；开启监听时同步写监听环）");
}
