/* PureVox — AI 麦克风降噪工具
 * Copyright (C) 2024-2026 a2heng <752848283@qq.com>
 *
 * PureVox is licensed under the GNU General Public License v3.0 or
 * later (GPL-3.0-or-later).  See LICENSE for details.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * The built-in AI models are NOT covered by the GPL; they are the
 * property of a2heng and may only be used with PureVox under
 * authorization.  See MODEL-LICENSE.md for details.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * pvpipe — PureVox 原生 PipeWire 桥接（纯 C 共享库，仅 Linux，ctypes 绑定见 pvpipe.py）。
 *
 * 为什么用原生 PipeWire（取代旧 GStreamer / JACK）：
 *   - 格式协商时直接声明 F32 单声道 48000Hz，PipeWire 内置重采样 + 声道
 *     转换，模型永远拿 48k 单声道，输出自动上混到目标设备声道数——
 *     不存在"一个通道一个模型/通道不匹配/采样率不齐"。
 *   - 无 JACK 依赖（libjack/jackdbus-detect），更现代。
 *
 * 结构（PwBridge）：
 *   - input 流   （Stream/Input/Audio，PW_KEY_TARGET_OBJECT=源节点名）
 *   - output 流  （Stream/Output/Audio，目标=输出节点名）
 *   - monitor 流 （可选，目标=监听节点名；同一路降噪音频）
 *   - far 流     （可选，AEC far-end，stream.capture.sink 监听目标 sink 输出）
 *   进程回调（实时/主循环线程）只做无锁 SPSC 环形缓冲搬运，Python 线程
 *   通过 pvpipe.py 读取→降噪→写入。
 *
 * 关键约束（延续旧 C++ 实现）：
 *   - 所有 pw_stream 操作经 _run_on_loop 在 PipeWire 主循环线程执行
 *     （pw_loop_invoke + 条件变量同步；block 参数不可靠）。
 *   - 进程回调禁用锁/分配——用 GCC __atomic 内置实现无锁 SPSC 环。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <pthread.h>

#include <pipewire/pipewire.h>
#include <spa/param/audio/format-utils.h>

/* ── 无锁 SPSC 环形缓冲（进程回调实时安全，禁锁/禁分配）────────────── */

typedef struct {
    size_t cap_;
    size_t mask_;
    float* buf_;
    size_t w_;          /* 只由生产者/消费者各自一地方修改，另一侧只读，atomic 读访 */
    size_t r_;
} SPSCRing;

static void spsc_init(SPSCRing* r, size_t capacity) {
    size_t cap = 1;
    while (cap < capacity) cap <<= 1;
    r->cap_ = cap;
    r->mask_ = cap - 1;
    r->buf_ = (float*)calloc(cap, sizeof(float));
    r->w_ = 0;
    r->r_ = 0;
}

static void spsc_free(SPSCRing* s) {
    free(s->buf_);
    s->buf_ = NULL;
}

static size_t spsc_available(const SPSCRing* s) {
    size_t w = __atomic_load_n(&s->w_, __ATOMIC_ACQUIRE);
    size_t r = __atomic_load_n(&s->r_, __ATOMIC_ACQUIRE);
    return w - r;
}

static void spsc_write(SPSCRing* s, const float* data, size_t n) {
    size_t w = __atomic_load_n(&s->w_, __ATOMIC_RELAXED);
    size_t r = __atomic_load_n(&s->r_, __ATOMIC_ACQUIRE);
    size_t free = s->cap_ - (w - r);
    if (free < n) {
        size_t drop = n - free;
        r += drop;
        __atomic_store_n(&s->r_, r, __ATOMIC_RELEASE);
    }
    for (size_t i = 0; i < n; ++i) s->buf_[(w + i) & s->mask_] = data[i];
    __atomic_store_n(&s->w_, w + n, __ATOMIC_RELEASE);
}

/* 满则丢新：写不下时只写头部（丢弃本次写入的新数据），不覆盖旧数据 */
static void spsc_write_drop_new(SPSCRing* s, const float* data, size_t n) {
    size_t w = __atomic_load_n(&s->w_, __ATOMIC_RELAXED);
    size_t r = __atomic_load_n(&s->r_, __ATOMIC_ACQUIRE);
    size_t free = s->cap_ - (w - r);
    if (free < n) {
        if (free == 0) return;
        n = free;
    }
    for (size_t i = 0; i < n; ++i) s->buf_[(w + i) & s->mask_] = data[i];
    __atomic_store_n(&s->w_, w + n, __ATOMIC_RELEASE);
}

static size_t spsc_read(SPSCRing* s, float* out, size_t n) {
    size_t w = __atomic_load_n(&s->w_, __ATOMIC_ACQUIRE);
    size_t r = __atomic_load_n(&s->r_, __ATOMIC_RELAXED);
    size_t avail = w - r;
    if (avail < n) n = avail;
    for (size_t i = 0; i < n; ++i) out[i] = s->buf_[(r + i) & s->mask_];
    __atomic_store_n(&s->r_, r + n, __ATOMIC_RELEASE);
    return n;
}

static size_t spsc_read_or_silence(SPSCRing* s, float* out, size_t n) {
    size_t got = spsc_read(s, out, n);
    for (size_t i = got; i < n; ++i) out[i] = 0.0f;
    return got;
}

/* ── PwBridge ────────────────────────────────────────────────────────── */

#define RING_CAPACITY 96000 /* 2s @48kHz */

typedef struct StreamCtx StreamCtx;

struct StreamCtx {
    struct PwBridge* self;
    SPSCRing* ring;      /* 该流对应环 */
    int is_input;        /* 输入流=填环；输出流=从环读填缓冲 */
};

/* 隐藏定义：public API 走函数，struct 不透明（ctypes 从不解引用） */
typedef struct PwBridge {
    struct pw_main_loop* loop_;
    struct pw_context*   context_;
    struct pw_core*      core_;
    struct pw_stream*    in_stream_;
    struct pw_stream*    out_stream_;
    struct pw_stream*    mon_stream_;
    struct pw_stream*    far_stream_;
    StreamCtx* in_ctx_;
    StreamCtx* out_ctx_;
    StreamCtx* mon_ctx_;
    StreamCtx* far_ctx_;
    pthread_t  loop_thread_;
    int        thread_started_;
    int        pw_init_done_;
    int        running_;            /* atomic bool */
    SPSCRing in_ring_;
    SPSCRing out_ring_;
    SPSCRing mon_ring_;
    SPSCRing far_ring_;
    uint32_t  rate_;
    uint32_t  buffer_size_;
    char      last_error_[256];
} PwBridge;

/* ── 主循环线程同步执行（_run_on_loop）───────────────────────────────── */

typedef struct {
    void (*fn)(void* user);
    void* user;
    pthread_mutex_t* mtx;
    pthread_cond_t*  cond;
    int done; /* atomic bool */
} LoopWork;

static int loop_work_thunk(struct spa_loop* loop, bool async, uint32_t seq,
                           const void* data, size_t size, void* user_data) {
    LoopWork* w = (LoopWork*)user_data;
    w->fn(w->user);
    __atomic_store_n(&w->done, 1, __ATOMIC_RELEASE);
    pthread_cond_signal(w->cond);
    (void)loop; (void)async; (void)seq; (void)data; (void)size;
    return 0;
}

/* 在 PipeWire 主循环线程同步执行 fn。主循环未运行（running_=false）时内联执行。 */
static void run_on_loop(PwBridge* self, void (*fn)(void* user), void* user) {
    if (self->loop_ == NULL || !__atomic_load_n(&self->running_, __ATOMIC_ACQUIRE)) {
        fn(user);
        return;
    }
    pthread_mutex_t mtx = PTHREAD_MUTEX_INITIALIZER;
    pthread_cond_t  cond = PTHREAD_COND_INITIALIZER;
    LoopWork work;
    work.fn = fn;
    work.user = user;
    work.mtx = &mtx;
    work.cond = &cond;
    __atomic_store_n(&work.done, 0, __ATOMIC_RELEASE);
    int res = pw_loop_invoke(pw_main_loop_get_loop(self->loop_), &loop_work_thunk,
                             0, NULL, 0, false, &work);
    if (res < 0) {
        fn(user);   /* invoke 失败：内联执行兜底 */
        return;
    }
    pthread_mutex_lock(&mtx);
    while (!__atomic_load_n(&work.done, __ATOMIC_ACQUIRE))
        pthread_cond_wait(&cond, &mtx);
    pthread_mutex_unlock(&mtx);
}

/* ── 流回调 ──────────────────────────────────────────────────────────── */

static void on_process(void* userdata) {
    StreamCtx* ctx = (StreamCtx*)userdata;
    PwBridge* self = ctx->self;
    struct pw_stream* stream = NULL;
    if (ctx->ring == &self->far_ring_) stream = self->far_stream_;
    else if (ctx->is_input)           stream = self->in_stream_;
    else stream = (ctx->ring == &self->mon_ring_) ? self->mon_stream_ : self->out_stream_;
    if (!stream) return;

    struct pw_buffer* b = pw_stream_dequeue_buffer(stream);
    if (!b) return;
    struct spa_buffer* buf = b->buffer;
    if (buf->datas[0].data && buf->datas[0].chunk) {
        if (ctx->is_input) {
            size_t n = buf->datas[0].chunk->size / sizeof(float);
            if (n > 0) spsc_write(ctx->ring, (const float*)buf->datas[0].data, n);
        } else {
            size_t n = buf->datas[0].chunk->size / sizeof(float);
            if (n == 0) n = buf->datas[0].maxsize / sizeof(float);
            float* data = (float*)buf->datas[0].data;
            spsc_read_or_silence(ctx->ring, data, n);
            buf->datas[0].chunk->size = n * sizeof(float);
        }
    }
    pw_stream_queue_buffer(stream, b);
}

static void on_param_changed(void* userdata, uint32_t id, const struct spa_pod* param) {
    if (param == NULL || id != SPA_PARAM_Format) return;
    StreamCtx* ctx = (StreamCtx*)userdata;
    PwBridge* self = ctx->self;
    struct spa_audio_info_raw info = { 0 };
    if (spa_format_audio_raw_parse(param, &info) < 0) return;
    /* 协商出的格式：理论上就是请求的 F32 单声道 48000（PipeWire 负责转换） */
    if (info.rate != 0) self->rate_ = info.rate;
}

/* 创建并连接一条流（必须在主循环线程执行）。失败写入 last_error_。 */
static struct pw_stream* create_stream(PwBridge* self, const char* name, enum pw_direction direction,
                                const char* target, SPSCRing* ring,
                                StreamCtx** out_ctx, bool capture_sink) {
    struct pw_properties* props = pw_properties_new(
        PW_KEY_NODE_NAME, name,
        PW_KEY_NODE_DESCRIPTION, "PureVox",
        PW_KEY_MEDIA_CLASS,
            direction == PW_DIRECTION_INPUT ? "Stream/Input/Audio" : "Stream/Output/Audio",
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Communication",
        PW_KEY_TARGET_OBJECT, target,
        NULL);
    if (!props) {
        snprintf(self->last_error_, sizeof(self->last_error_), "创建流失败: %s", name);
        return NULL;
    }
    if (capture_sink) {
        /* AEC far-end：监听目标 sink 的播出输出（替代监听 .monitor 源节点） */
        pw_properties_set(props, PW_KEY_STREAM_CAPTURE_SINK, "true");
    }

    StreamCtx* ctx = (StreamCtx*)calloc(1, sizeof(StreamCtx));
    ctx->self = self;
    ctx->is_input = (direction == PW_DIRECTION_INPUT);
    ctx->ring = ring;
    if (out_ctx) *out_ctx = ctx;

    /* events 须在流生命周期内保持有效（pw_stream 只存指针不拷贝） */
    static struct pw_stream_events* g_events = NULL;
    if (g_events == NULL) {
        g_events = (struct pw_stream_events*)calloc(1, sizeof(struct pw_stream_events));
        g_events->version = PW_VERSION_STREAM_EVENTS;
        g_events->process = &on_process;
        g_events->param_changed = &on_param_changed;
    }

    struct pw_stream* stream = pw_stream_new_simple(
        pw_main_loop_get_loop(self->loop_), name, props, g_events, ctx);
    if (!stream) {
        free(ctx);
        if (out_ctx) *out_ctx = NULL;
        snprintf(self->last_error_, sizeof(self->last_error_), "创建流失败: %s", name);
        return NULL;
    }

    struct spa_audio_info_raw info = { 0 };
    info.format = SPA_AUDIO_FORMAT_F32;
    info.rate = 48000;
    info.channels = 1;

    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
    const struct spa_pod* params[1];
    params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat, &info);

    if (pw_stream_connect(stream, direction, PW_ID_ANY,
                          (enum pw_stream_flags)(PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS),
                          params, 1) < 0) {
        pw_stream_destroy(stream);
        free(ctx);
        if (out_ctx) *out_ctx = NULL;
        snprintf(self->last_error_, sizeof(self->last_error_), "连接流失败: %s → %s", name, target);
        return NULL;
    }
    return stream;
}

static void* loop_thread_fn(void* user) {
    PwBridge* self = (PwBridge*)user;
    pw_main_loop_run(self->loop_);
    return NULL;
}

/* ── _run_on_loop 用 work 结构（C 版闭包）────────────────────────────── */

typedef struct { PwBridge* self; const char* in; const char* out; const char* mon; int ok; } OpenWork;
typedef struct { PwBridge* self; } SelfWork;
typedef struct { PwBridge* self; const char* name; int enabled; int result; } NameWork;

static void open_work(void* u) {
    OpenWork* w = (OpenWork*)u;
    PwBridge* self = w->self;
    if (w->in && w->in[0]) {
        self->in_stream_ = create_stream(self, "PureVox-input", PW_DIRECTION_INPUT,
                                         w->in, &self->in_ring_, &self->in_ctx_, false);
        if (!self->in_stream_) w->ok = 0;
    }
    if (w->ok && w->out && w->out[0]) {
        self->out_stream_ = create_stream(self, "PureVox-output", PW_DIRECTION_OUTPUT,
                                          w->out, &self->out_ring_, &self->out_ctx_, false);
        if (!self->out_stream_) w->ok = 0;
    }
    if (w->ok && w->mon && w->mon[0]) {
        self->mon_stream_ = create_stream(self, "PureVox-monitor", PW_DIRECTION_OUTPUT,
                                          w->mon, &self->mon_ring_, &self->mon_ctx_, false);
        if (!self->mon_stream_) w->ok = 0;
    }
    if (w->ok && !self->in_stream_ && !self->out_stream_) {
        snprintf(self->last_error_, sizeof(self->last_error_), "未配置任何输入/输出目标");
        w->ok = 0;
    }
}

static void close_work(void* u) {
    PwBridge* self = ((SelfWork*)u)->self;
    if (self->in_stream_)  { pw_stream_destroy(self->in_stream_);  self->in_stream_ = NULL; }
    if (self->out_stream_) { pw_stream_destroy(self->out_stream_); self->out_stream_ = NULL; }
    if (self->mon_stream_) { pw_stream_destroy(self->mon_stream_); self->mon_stream_ = NULL; }
    if (self->far_stream_) { pw_stream_destroy(self->far_stream_); self->far_stream_ = NULL; }
}

static void set_monitor_work(void* u) {
    NameWork* w = (NameWork*)u;
    PwBridge* self = w->self;
    if (w->enabled && !self->mon_stream_) {
        self->mon_stream_ = create_stream(self, "PureVox-monitor", PW_DIRECTION_OUTPUT,
                                          w->name, &self->mon_ring_, &self->mon_ctx_, false);
        w->result = (self->mon_stream_ != NULL);
    } else if (!w->enabled && self->mon_stream_) {
        pw_stream_destroy(self->mon_stream_);
        self->mon_stream_ = NULL;
        if (self->mon_ctx_) { free(self->mon_ctx_); self->mon_ctx_ = NULL; }
        w->result = 1;
    } else {
        w->result = 1;
    }
}

static void set_far_work(void* u) {
    NameWork* w = (NameWork*)u;
    PwBridge* self = w->self;
    if (w->enabled && !self->far_stream_ && w->name && w->name[0]) {
        self->far_stream_ = create_stream(self, "PureVox-far", PW_DIRECTION_INPUT,
                                          w->name, &self->far_ring_, &self->far_ctx_, true);
        w->result = (self->far_stream_ != NULL);
    } else if (!w->enabled && self->far_stream_) {
        pw_stream_destroy(self->far_stream_);
        self->far_stream_ = NULL;
        if (self->far_ctx_) { free(self->far_ctx_); self->far_ctx_ = NULL; }
        w->result = 1;
    } else {
        w->result = w->enabled ? (self->far_stream_ != NULL) : 1;
    }
}

/* ── C API（供 pvpipe.py ctypes 调用）────────────────────────────────── */

void pvb_close(PwBridge* self);  /* 供 pvb_free 前向引用 */

PwBridge* pvb_new(void) {
    PwBridge* self = (PwBridge*)calloc(1, sizeof(PwBridge));
    if (!self) return NULL;
    spsc_init(&self->in_ring_, RING_CAPACITY);
    spsc_init(&self->out_ring_, RING_CAPACITY);
    spsc_init(&self->mon_ring_, RING_CAPACITY);
    spsc_init(&self->far_ring_, RING_CAPACITY);
    if (!self->in_ring_.buf_ || !self->out_ring_.buf_ ||
        !self->mon_ring_.buf_ || !self->far_ring_.buf_) {
        /* 任一环分配失败：释放已分配环与结构 */
        spsc_free(&self->in_ring_);
        spsc_free(&self->out_ring_);
        spsc_free(&self->mon_ring_);
        spsc_free(&self->far_ring_);
        free(self);
        return NULL;
    }
    self->rate_ = 48000;
    self->buffer_size_ = 0;
    self->last_error_[0] = '\0';
    return self;
}

void pvb_free(PwBridge* self) {
    if (!self) return;
    pvb_close(self);
    spsc_free(&self->in_ring_);
    spsc_free(&self->out_ring_);
    spsc_free(&self->mon_ring_);
    spsc_free(&self->far_ring_);
    free(self);
}

int pvb_open(PwBridge* self, const char* input_name, const char* output_name,
             const char* monitor_name) {
    if (self->loop_) return 1;
    self->last_error_[0] = '\0';
    pw_init(NULL, NULL);
    self->pw_init_done_ = 1;
    self->loop_ = pw_main_loop_new(NULL);
    if (!self->loop_) { strncpy(self->last_error_, "pw_main_loop_new 失败", sizeof(self->last_error_) - 1); return 0; }
    self->context_ = pw_context_new(pw_main_loop_get_loop(self->loop_), NULL, 0);
    if (!self->context_) { strncpy(self->last_error_, "pw_context_new 失败", sizeof(self->last_error_) - 1); return 0; }
    self->core_ = pw_context_connect(self->context_, NULL, 0);
    if (!self->core_) { strncpy(self->last_error_, "pw_context_connect 失败（PipeWire 未运行）", sizeof(self->last_error_) - 1); return 0; }

    /* 先启动主循环线程，之后所有 pw_stream 操作都经 _run_on_loop 执行 */
    __atomic_store_n(&self->running_, 1, __ATOMIC_RELEASE);
    self->thread_started_ = 0;
    if (pthread_create(&self->loop_thread_, NULL, &loop_thread_fn, self) == 0) {
        self->thread_started_ = 1;
    }

    OpenWork w;
    w.self = self; w.in = input_name; w.out = output_name; w.mon = monitor_name; w.ok = 1;
    if (self->thread_started_) {
        run_on_loop(self, &open_work, &w);
    } else {
        /* 主循环线程启动失败：绝不能走 pw_loop_invoke（会阻塞在条件变量上），
         * 直接内联创建（失败即关闭，避免死锁）。 */
        open_work(&w);
        if (!w.ok) {
            strncpy(self->last_error_, "主循环线程启动失败", sizeof(self->last_error_) - 1);
        }
    }
    if (!w.ok) {
        pvb_close(self);
        return 0;
    }
    return 1;
}

void pvb_close(PwBridge* self) {
    if (self->thread_started_) {
        SelfWork w = { self };
        run_on_loop(self, &close_work, &w);
        __atomic_store_n(&self->running_, 0, __ATOMIC_RELEASE);
        pw_main_loop_quit(self->loop_);
        pthread_join(self->loop_thread_, NULL);
        self->thread_started_ = 0;
        self->loop_thread_ = 0;
    }
    if (self->in_ctx_)  { free(self->in_ctx_);  self->in_ctx_ = NULL; }
    if (self->out_ctx_) { free(self->out_ctx_); self->out_ctx_ = NULL; }
    if (self->mon_ctx_) { free(self->mon_ctx_); self->mon_ctx_ = NULL; }
    if (self->far_ctx_) { free(self->far_ctx_); self->far_ctx_ = NULL; }
    if (self->core_) { pw_core_disconnect(self->core_); self->core_ = NULL; }
    if (self->context_) { pw_context_destroy(self->context_); self->context_ = NULL; }
    if (self->loop_) { pw_main_loop_destroy(self->loop_); self->loop_ = NULL; }
    if (self->pw_init_done_) { pw_deinit(); self->pw_init_done_ = 0; }
}

int pvb_active(const PwBridge* self) {
    return self->loop_ != NULL;
}

const char* pvb_last_error(const PwBridge* self) {
    return self->last_error_;
}

uint32_t pvb_sample_rate(const PwBridge* self) {
    return self->rate_;
}

uint32_t pvb_buffer_size(const PwBridge* self) {
    return self->buffer_size_;
}

int pvb_set_monitor(PwBridge* self, const char* monitor_name, int enabled) {
    if (!self->core_) return 0;
    NameWork w;
    w.self = self; w.name = monitor_name; w.enabled = enabled; w.result = 0;
    run_on_loop(self, &set_monitor_work, &w);
    return w.result;
}

int pvb_set_far(PwBridge* self, const char* sink_name, int enabled) {
    if (!self->core_) return 0;
    NameWork w;
    w.self = self; w.name = sink_name; w.enabled = enabled; w.result = 0;
    run_on_loop(self, &set_far_work, &w);
    return w.result;
}

size_t pvb_read(PwBridge* self, float* out, size_t n) {
    return spsc_read(&self->in_ring_, out, n);
}

size_t pvb_read_far(PwBridge* self, float* out, size_t n) {
    return spsc_read(&self->far_ring_, out, n);
}

void pvb_write(PwBridge* self, const float* data, size_t n) {
    if (n == 0) return;
    spsc_write_drop_new(&self->out_ring_, data, n);
    if (self->mon_stream_) spsc_write_drop_new(&self->mon_ring_, data, n);
}