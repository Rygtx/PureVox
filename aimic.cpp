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

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <vector>
#include <cmath>
#include <complex>
#include <cstring>
#include <algorithm>
#include <memory>
#include <cstdint>
#include <atomic>
#include <cstdio>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <chrono>

#ifdef _WIN32
#define NOMINMAX
#include <Windows.h>
#endif

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#include "onnxruntime_cxx_api.h"
#include "cpu_provider_factory.h"
#include "pffft.h"
#include "samplerate.h"

namespace py = pybind11;

// Clip audio sample to [-1, 1] range
inline float clip_sample(float x) {
    if (std::isnan(x) || std::isinf(x)) return 0.0f;
    if (x > 1.0f) return 1.0f;
    if (x < -1.0f) return -1.0f;
    return x;
}

// Clip all samples in a vector to [-1, 1] range
void clip_buffer(float* data, size_t len) {
    for (size_t i = 0; i < len; ++i) {
        data[i] = clip_sample(data[i]);
    }
}

static const size_t HOP_LENGTH = 1024;
static const float SAMPLE_RATE = 48000.0f;

// Forward declaration
class TseProcessor;


// ============================================================================
// VAD (Voice Activity Detection) gate — energy-based with onset/hang
// ============================================================================
class VadGate {
public:
    VadGate(float threshold_dbfs = -45.0f, float onset_ms = 20.0f,
            float hang_ms = 250.0f, float fs = 48000.0f, int hop = 480)
        : threshold_linear_(std::pow(10.0f, threshold_dbfs / 20.0f)),
          onset_frames_(std::max(1, static_cast<int>(onset_ms / 1000.0f * fs / hop))),
          hang_frames_(std::max(1, static_cast<int>(hang_ms / 1000.0f * fs / hop))),
          active_(false), voice_cnt_(0), silence_cnt_(0) {}

    void reset() {
        active_ = false;
        voice_cnt_ = 0;
        silence_cnt_ = 0;
    }

    /// Process audio frame: updates VAD state, zeroes samples if inactive.
    /// Returns current active state.
    bool process(float* samples, size_t n) {
        // Compute RMS
        float sq = 0.0f;
        for (size_t i = 0; i < n; ++i) {
            sq += samples[i] * samples[i];
        }
        float rms = (sq > 0.0f) ? std::sqrt(sq / static_cast<float>(n)) : 0.0f;
        bool is_voice = rms > threshold_linear_;

        if (is_voice) {
            voice_cnt_++;
            silence_cnt_ = 0;
        } else {
            silence_cnt_++;
            voice_cnt_ = 0;
        }

        if (!active_ && voice_cnt_ >= onset_frames_) {
            active_ = true;
        } else if (active_ && silence_cnt_ >= hang_frames_) {
            active_ = false;
        }

        if (!active_) {
            for (size_t i = 0; i < n; ++i) {
                samples[i] = 0.0f;
            }
        }

        return active_;
    }

    bool is_active() const { return active_; }

    void set_threshold(float dbfs) {
        threshold_linear_ = std::pow(10.0f, dbfs / 20.0f);
    }

    float threshold_dbfs() const {
        return 20.0f * std::log10(threshold_linear_);
    }

private:
    float threshold_linear_;
    int onset_frames_;
    int hang_frames_;
    bool active_;
    int voice_cnt_;
    int silence_cnt_;
};

// ============================================================================
// AGC (Automatic Gain Control) — replaces pre_gain in denoise/bypass modes.
// Closed-loop: measure VAD output RMS → compute target gain → EMA smooth → use as pre_gain.
// Window-based: only update gain when recent frames have voice; stop when trailing silence detected.
// ============================================================================
class AgcController {
public:
    AgcController(float target_dbfs = -20.0f, float call_interval_ms = 10.0f)
        : target_dbfs_(target_dbfs),
          target_linear_(std::pow(10.0f, target_dbfs / 20.0f)),
          gain_min_linear_(std::pow(10.0f, -30.0f / 20.0f)),
          gain_max_linear_(std::pow(10.0f, 30.0f / 20.0f)),
          silence_thr_linear_(std::pow(10.0f, -45.0f / 20.0f)),
          rms_floor_linear_(std::pow(10.0f, -60.0f / 20.0f)),
          smoothed_gain_linear_(1.0f), rms_ema_(0.0f),
          initialized_(false), enabled_(false),
          voice_active_(false), silent_tail_count_(0)
    {
        float dt_tick = call_interval_ms / 1000.0f;
        // Asymmetric: attack fast (10ms), release slow (150ms)
        attack_alpha_  = 1.0f - std::exp(-dt_tick / 0.010f);
        release_alpha_ = 1.0f - std::exp(-dt_tick / 0.150f);
        // Silent decay: 1 second half-life
        decay_factor_ = std::pow(0.5f, dt_tick);
        // Gain dead zone: no update if change < 0.5 dB
        dead_zone_ = std::pow(10.0f, 0.5f / 20.0f);
        // RMS EMA: tau=200ms
        rms_alpha_ = 1.0f - std::exp(-dt_tick / 0.200f);
    }

    void reset() {
        smoothed_gain_linear_ = 1.0f;
        rms_ema_ = 0.0f;
        initialized_ = false;
        voice_active_ = false;
        silent_tail_count_ = 0;
    }

    /// Feed RMS measurement. EMA smoothing.
    void update_rms(float rms_linear) {
        bool is_voice = rms_linear > silence_thr_linear_;

        if (is_voice) {
            silent_tail_count_ = 0;
            voice_active_ = true;
        } else {
            silent_tail_count_++;
            if (silent_tail_count_ >= SILENT_TAIL_FRAMES)
                voice_active_ = false;
        }

        if (!voice_active_) return;
        if (rms_linear <= silence_thr_linear_) return;

        // RMS EMA smoothing
        if (rms_ema_ == 0.0f) {
            rms_ema_ = rms_linear;
        } else {
            rms_ema_ = rms_alpha_ * rms_linear + (1.0f - rms_alpha_) * rms_ema_;
        }
    }

    /// Compute and smooth gain. Returns linear gain to use as pre_gain.
    float tick() {
        if (!enabled_) return 1.0f;

        // Silent: positive gain decays slowly
        if (!voice_active_) {
            if (smoothed_gain_linear_ > 1.0f) {
                smoothed_gain_linear_ *= decay_factor_;
                if (smoothed_gain_linear_ < 1.0f)
                    smoothed_gain_linear_ = 1.0f;
            }
            return smoothed_gain_linear_;
        }

        if (rms_ema_ == 0.0f) return smoothed_gain_linear_;

        float rms = rms_ema_;
        if (rms < rms_floor_linear_) rms = rms_floor_linear_;

        float target_gain = target_linear_ / rms;
        if (target_gain < gain_min_linear_) target_gain = gain_min_linear_;
        if (target_gain > gain_max_linear_) target_gain = gain_max_linear_;

        if (!initialized_) {
            initialized_ = true;
            smoothed_gain_linear_ = target_gain;
        } else {
            // Dead zone: no update if change too small
            float ratio = target_gain / smoothed_gain_linear_;
            if (ratio > (1.0f / dead_zone_) && ratio < dead_zone_) {
                return smoothed_gain_linear_;
            }
            // Asymmetric smoothing
            float alpha = (target_gain < smoothed_gain_linear_) ? attack_alpha_ : release_alpha_;
            smoothed_gain_linear_ = alpha * target_gain +
                                    (1.0f - alpha) * smoothed_gain_linear_;
        }

        return smoothed_gain_linear_;
    }

    float get_current_gain_linear() const { return smoothed_gain_linear_; }
    float get_current_gain_db() const {
        return 20.0f * std::log10(smoothed_gain_linear_ > 0.0f ? smoothed_gain_linear_ : 1e-10f);
    }

    bool is_voice_active() const { return voice_active_; }

    void set_enabled(bool enabled, float initial_gain_db = 0.0f) {
        if (enabled && !enabled_) {
            smoothed_gain_linear_ = std::pow(10.0f, initial_gain_db / 20.0f);
            rms_ema_ = 0.0f;
            initialized_ = false;
            voice_active_ = false;
            silent_tail_count_ = 0;
        }
        enabled_ = enabled;
    }
    bool is_enabled() const { return enabled_; }

    void set_target(float dbfs) {
        target_dbfs_ = dbfs;
        target_linear_ = std::pow(10.0f, dbfs / 20.0f);
    }
    float target_dbfs() const { return target_dbfs_; }

private:
    static constexpr int SILENT_TAIL_FRAMES = 15;

    float target_dbfs_;
    float target_linear_;
    float gain_min_linear_;
    float gain_max_linear_;
    float silence_thr_linear_;
    float rms_floor_linear_;
    float attack_alpha_;
    float release_alpha_;
    float decay_factor_;
    float dead_zone_;
    float rms_alpha_;
    float smoothed_gain_linear_;
    float rms_ema_;
    bool initialized_;
    bool enabled_;
    bool voice_active_;
    int silent_tail_count_;
};

// ============================================================================
// Compressor — RMS envelope voice compressor
// ============================================================================

class Compressor {
public:

    Compressor(
        float threshold_db = -20.0f,
        float ratio = 3.0f,
        float attack_ms = 15.0f,
        float release_ms = 180.0f,
        float knee_db = 8.0f,
        float makeup_db = 4.0f,
        float fs = 48000.0f)

        :
        threshold_db_(threshold_db),
        ratio_(ratio),
        knee_db_(knee_db),
        makeup_db_(makeup_db),
        enabled_(false),
        envelope_(0.0f),
        gain_smooth_(1.0f)
    {
        set_detector_attack(attack_ms, fs);
        set_detector_release(release_ms, fs);
        set_gain_attack(25.0f, fs);
        set_gain_release(220.0f, fs);
    }

    void set_detector_attack(float ms, float fs) {
        detector_attack_ms_ = ms;
        detector_attack_alpha_ = 1.0f - std::exp(-1.0f / (ms * 0.001f * fs));
    }
    void set_detector_release(float ms, float fs) {
        detector_release_ms_ = ms;
        detector_release_alpha_ = 1.0f - std::exp(-1.0f / (ms * 0.001f * fs));
    }
    void set_gain_attack(float ms, float fs) {
        gain_attack_alpha_ = 1.0f - std::exp(-1.0f / (ms * 0.001f * fs));
    }
    void set_gain_release(float ms, float fs) {
        gain_release_alpha_ = 1.0f - std::exp(-1.0f / (ms * 0.001f * fs));
    }

    void set_attack(float ms, float fs = 48000.0f) { set_detector_attack(ms, fs); }
    void set_release(float ms, float fs = 48000.0f) { set_detector_release(ms, fs); }
    void set_threshold(float db) { threshold_db_ = db; }
    float get_threshold() const { return threshold_db_; }
    void set_ratio(float r) { ratio_ = (r < 1.0f) ? 1.0f : r; }
    float get_ratio() const { return ratio_; }
    void set_attack_ms(float ms) { set_detector_attack(ms, 48000.0f); }
    float get_attack_ms() const { return detector_attack_ms_; }
    void set_release_ms(float ms) { set_detector_release(ms, 48000.0f); }
    float get_release_ms() const { return detector_release_ms_; }
    void set_knee(float db) { knee_db_ = db; }
    float get_knee() const { return knee_db_; }
    void set_makeup(float db) { makeup_db_ = db; }
    float get_makeup() const { return makeup_db_; }
    void set_enabled(bool en) { enabled_ = en; }
    bool is_enabled() const { return enabled_; }

    void reset() {
        envelope_ = 0.0f;
        gain_smooth_ = 1.0f;
    }

    void process(float* data, size_t len) {
        if (!enabled_) return;

        for (size_t i = 0; i < len; ++i) {
            // RMS² detector — single exponential smoothing, no branching
            float x2 = data[i] * data[i];
            float alpha = (x2 > envelope_) ? detector_attack_alpha_ : detector_release_alpha_;
            envelope_ += alpha * (x2 - envelope_);

            float env_db = (envelope_ > 1e-12f)
                ? 10.0f * std::log10(envelope_)
                : -120.0f;

            // Compressor curve (standard soft knee centered on threshold)
            float over = env_db - threshold_db_;
            float gr_db = 0.0f;
            if (over > 0.0f) {
                if (knee_db_ > 0.0f && over < knee_db_) {
                    float t = over / knee_db_;
                    gr_db = (1.0f / ratio_ - 1.0f) * over * t * 0.5f;
                } else {
                    gr_db = (1.0f / ratio_ - 1.0f) * over;
                }
            }

            float gain_target = std::pow(10.0f, (gr_db + makeup_db_) / 20.0f);

            // Gain smoothing (separate attack/release)
            if (gain_target < gain_smooth_) {
                gain_smooth_ = gain_attack_alpha_ * gain_target + (1.0f - gain_attack_alpha_) * gain_smooth_;
            } else {
                gain_smooth_ = gain_release_alpha_ * gain_target + (1.0f - gain_release_alpha_) * gain_smooth_;
            }

            // Apply gain + soft limiter to prevent clip from makeup
            float out = data[i] * gain_smooth_;
            data[i] = std::tanh(out);
        }
    }

private:
    float threshold_db_;
    float ratio_;
    float knee_db_;
    float makeup_db_;
    float detector_attack_alpha_;
    float detector_attack_ms_;
    float detector_release_alpha_;
    float detector_release_ms_;
    float gain_attack_alpha_;
    float gain_release_alpha_;
    bool enabled_;
    float envelope_;
    float gain_smooth_;
};

std::vector<float> create_hann_window(size_t frame_size) {
    std::vector<float> window(frame_size);
    for (size_t i = 0; i < frame_size; ++i) {
        float hann = 0.5f - 0.5f * std::cos(2.0f * M_PI * i / frame_size);
        window[i] = std::sqrt(hann + 1e-10f);
    }
    return window;
}

// ============================================================================
// Audio Effects Components (Always running when processing is active)
// ============================================================================

static const int EQ_BANDS = 61;
static const float EQ_FREQS[EQ_BANDS] = {
    20.0f, 22.4f, 25.0f, 28.0f, 31.5f, 35.5f, 40.0f, 45.0f, 50.0f, 56.0f,
    63.0f, 71.0f, 80.0f, 90.0f, 100.0f, 112.0f, 125.0f, 140.0f, 160.0f, 180.0f,
    200.0f, 224.0f, 250.0f, 280.0f, 315.0f, 355.0f, 400.0f, 450.0f, 500.0f, 560.0f,
    630.0f, 710.0f, 800.0f, 900.0f, 1000.0f, 1120.0f, 1250.0f, 1400.0f, 1600.0f, 1800.0f,
    2000.0f, 2240.0f, 2500.0f, 2800.0f, 3150.0f, 3550.0f, 4000.0f, 4500.0f, 5000.0f, 5600.0f,
    6300.0f, 7100.0f, 8000.0f, 9000.0f, 10000.0f, 11200.0f, 12500.0f, 14000.0f, 16000.0f, 18000.0f,
    20000.0f
};
static const float EQ_Q = 1.414f;

struct BiquadCoeff {
    float b0, b1, b2, a1, a2;
    float x1, x2, y1, y2;
    BiquadCoeff() : b0(1), b1(0), b2(0), a1(0), a2(0), x1(0), x2(0), y1(0), y2(0) {}
    void reset_state() { x1 = x2 = y1 = y2 = 0; }
};

BiquadCoeff design_peaking_eq(float freq, float gain_db, float q, float sample_rate) {
    BiquadCoeff coeff;
    float A = std::pow(10.0f, gain_db / 40.0f);
    float w0 = 2.0f * M_PI * freq / sample_rate;
    float cos_w0 = std::cos(w0);
    float sin_w0 = std::sin(w0);
    float alpha = sin_w0 / (2.0f * q);
    float a0 = 1.0f + alpha / A;
    coeff.b0 = (1.0f + alpha * A) / a0;
    coeff.b1 = (-2.0f * cos_w0) / a0;
    coeff.b2 = (1.0f - alpha * A) / a0;
    coeff.a1 = (-2.0f * cos_w0) / a0;
    coeff.a2 = (1.0f - alpha / A) / a0;
    return coeff;
}

// ============================================================================
// Thread-safe ring buffer for TSE async pipeline
// ============================================================================
class TseRingBuffer {
public:
    TseRingBuffer(size_t capacity) 
        : buf_(capacity, 0.0f), capacity_(capacity), write_pos_(0), count_(0) {}

    // Non-blocking write. Returns false if insufficient space.
    bool write(const float* data, size_t len) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (count_ + len > capacity_) return false;
        size_t end_pos = write_pos_ + len;
        if (end_pos <= capacity_) {
            std::memcpy(buf_.data() + write_pos_, data, len * sizeof(float));
        } else {
            size_t first = capacity_ - write_pos_;
            std::memcpy(buf_.data() + write_pos_, data, first * sizeof(float));
            std::memcpy(buf_.data(), data + first, (len - first) * sizeof(float));
        }
        write_pos_ = (write_pos_ + len) % capacity_;
        count_ += len;
        cv_.notify_one();
        return true;
    }

    // Non-blocking read. Returns false if insufficient data.
    bool try_read(float* dest, size_t len) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (count_ < len) return false;
        size_t read_pos = (write_pos_ + capacity_ - count_) % capacity_;
        size_t end_pos = read_pos + len;
        if (end_pos <= capacity_) {
            std::memcpy(dest, buf_.data() + read_pos, len * sizeof(float));
        } else {
            size_t first = capacity_ - read_pos;
            std::memcpy(dest, buf_.data() + read_pos, first * sizeof(float));
            std::memcpy(dest + first, buf_.data(), (len - first) * sizeof(float));
        }
        count_ -= len;
        return true;
    }

    // Blocking read with timeout (ms). Returns false on timeout.
    bool read_wait(float* dest, size_t len, int timeout_ms) {
        std::unique_lock<std::mutex> lock(mutex_);
        if (!cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms),
                          [this, len] { return count_ >= len; })) {
            return false;
        }
        size_t read_pos = (write_pos_ + capacity_ - count_) % capacity_;
        size_t end_pos = read_pos + len;
        if (end_pos <= capacity_) {
            std::memcpy(dest, buf_.data() + read_pos, len * sizeof(float));
        } else {
            size_t first = capacity_ - read_pos;
            std::memcpy(dest, buf_.data() + read_pos, first * sizeof(float));
            std::memcpy(dest + first, buf_.data(), (len - first) * sizeof(float));
        }
        count_ -= len;
        return true;
    }

    size_t available() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return count_;
    }

    size_t capacity() const { return capacity_; }

    void clear() {
        std::lock_guard<std::mutex> lock(mutex_);
        std::fill(buf_.begin(), buf_.end(), 0.0f);
        write_pos_ = 0;
        count_ = 0;
    }

private:
    std::vector<float> buf_;
    size_t capacity_;
    size_t write_pos_;
    size_t count_;
    mutable std::mutex mutex_;
    std::condition_variable cv_;
};

// ============================================================================
// TSE Processor — full target speaker extraction pipeline
// Uses pffft for STFT, onnxruntime for inference
// ============================================================================
// TSE Processor — 48kHz, 2048 NFFT, 1024 HOP, streaming ONNX
// ONNX inputs: spec_frame, enr_spec, cache_in
// ONNX outputs: enh_frame, cache_out
// Enrollment: STFT → enr_spec(1,2,Te,1025) raw complex spectrum
// ============================================================================

// AEC (Acoustic Echo Cancellation) — aec9 ONNX streaming
//  NFFT=2048, HOP=1024, FREQ=1025, Mel-256, delay line, complex mask
//  External interface: 1024-sample chunks (AEC_HOP=1024).
// ============================================================================

class AecProcessor {
public:
    static const int AEC_FS = 48000;
    static const int AEC_NFFT = 2048;
    static const int AEC_HOP = 1024;       // internal processing hop
    static const int AEC_FREQ = 1025;      // NFFT/2 + 1
    static const int AEC_SPEC_SIZE = AEC_FREQ * 2;  // 2050 floats flat

    // ── aec9 cache fallback sizes (auto-detected from ONNX at init) ──
    // FrontEncoder conv [(16,10,256),(24,10,128),(36,10,64),(48,9,32)] → 108544
    static constexpr int RES_ENC_CONV_SIZE  = 108544;
    // FrontEncoder tfa [32,48,72,96] → 248
    static constexpr int RES_ENC_TFA_SIZE   = 248;
    static constexpr int MIC_ENC_CONV_SIZE  = 108544;
    static constexpr int MIC_ENC_TFA_SIZE   = 248;
    // DeepEncoder conv [(72,0,32),(96,0,32)] → 0 (ONNX optimized away)
    // DeepEncoder tfa [144,192] → 336
    static constexpr int DEEP_ENC_TFA_SIZE  = 336;
    // Decoder conv [(96,0,32),(72,2,32),(48,2,64),(32,0,128)] → 10752
    static constexpr int DEC_CONV_SIZE      = 10752;
    // Decoder tfa [192,144,96,64] → 496
    static constexpr int DEC_TFA_SIZE       = 496;
    // DPGRNN inter [(1,32,96),(1,32,96)] → 6144
    static constexpr int INTER_SIZE         = 6144;
    // Derivative caches (1,1,1,256) → 256
    static constexpr int PREV_SIZE          = 256;
    // Delay buffer (1,3,52,256) → 39936
    static constexpr int DELAY_BUF_SIZE     = 39936;

    AecProcessor(const std::string& model_path)
        : mic_history_(AEC_NFFT, 0.0f),
          far_history_(AEC_NFFT, 0.0f),
          ola_accumulator_(AEC_NFFT, 0.0f),
          window_sum_(AEC_NFFT, 0.0f),
          mic_onnx_(AEC_SPEC_SIZE, 0.0f),
          far_onnx_(AEC_SPEC_SIZE, 0.0f),
          out_acc_pos_(0)
    {
        // sqrt-Hann window (periodic, matches Python: torch.hann_window(2048).pow(0.5))
        window_.resize(AEC_NFFT);
        for (int i = 0; i < AEC_NFFT; ++i) {
            float hann = 0.5f * (1.0f - std::cos(2.0f * M_PI * i / AEC_NFFT));
            window_[i] = std::sqrt(hann);
        }

        // Allocate caches with fallback sizes (will be overridden by ONNX auto-detect)
        res_enc_conv_.resize(RES_ENC_CONV_SIZE, 0.0f);
        res_enc_tfa_.resize(RES_ENC_TFA_SIZE, 0.0f);
        mic_enc_conv_.resize(MIC_ENC_CONV_SIZE, 0.0f);
        mic_enc_tfa_.resize(MIC_ENC_TFA_SIZE, 0.0f);
        deep_enc_tfa_.resize(DEEP_ENC_TFA_SIZE, 0.0f);
        dec_conv_.resize(DEC_CONV_SIZE, 0.0f);
        dec_tfa_.resize(DEC_TFA_SIZE, 0.0f);
        inter_.resize(INTER_SIZE, 0.0f);
        res_prev1_.resize(PREV_SIZE, 0.0f);
        res_prev2_.resize(PREV_SIZE, 0.0f);
        mic_prev1_.resize(PREV_SIZE, 0.0f);
        mic_prev2_.resize(PREV_SIZE, 0.0f);
        delay_buf_.resize(DELAY_BUF_SIZE, 0.0f);

        // pffft
        fft_in_   = static_cast<float*>(pffft_aligned_malloc(AEC_NFFT * sizeof(float)));
        fft_out_  = static_cast<float*>(pffft_aligned_malloc(AEC_NFFT * sizeof(float)));
        ifft_out_ = static_cast<float*>(pffft_aligned_malloc(AEC_NFFT * sizeof(float)));

        fft_plan_ = pffft_new_setup(AEC_NFFT, PFFFT_REAL);

        // ONNX session
        Ort::SessionOptions session_options;
        session_options.SetIntraOpNumThreads(1);
        session_options.SetInterOpNumThreads(1);
        session_options.SetExecutionMode(ORT_SEQUENTIAL);
        session_options.SetGraphOptimizationLevel(ORT_ENABLE_BASIC);
        env_ = std::make_shared<Ort::Env>(ORT_LOGGING_LEVEL_WARNING, "AecProcessor");

#ifdef _WIN32
        int wide_size = MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, nullptr, 0);
        std::wstring wide_path(wide_size, 0);
        MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, &wide_path[0], wide_size);
        Ort::Session session(*env_, wide_path.c_str(), session_options);
#else
        Ort::Session session(*env_, model_path.c_str(), session_options);
#endif
        session_ = std::make_shared<Ort::Session>(std::move(session));

        input_names_  = session_->GetInputNames();
        output_names_ = session_->GetOutputNames();

        // Auto-detect cache sizes from ONNX model shapes
        auto get_cache_size = [&](const std::string& name, size_t fallback) -> size_t {
            for (size_t i = 0; i < input_names_.size(); ++i) {
                if (input_names_[i] == name) {
                    auto shape = session_->GetInputTypeInfo(i)
                                     .GetTensorTypeAndShapeInfo().GetShape();
                    size_t total = 1;
                    for (auto d : shape) { if (d <= 0) return fallback; total *= (size_t)d; }
                    return total;
                }
            }
            return fallback;
        };

        auto resize_cache = [&](const std::string& name, std::vector<float>& v, size_t fallback) {
            size_t actual = get_cache_size(name, fallback);
            v.resize(actual, 0.0f);
        };

        resize_cache("res_enc_conv", res_enc_conv_, RES_ENC_CONV_SIZE);
        resize_cache("res_enc_tfa",  res_enc_tfa_,  RES_ENC_TFA_SIZE);
        resize_cache("mic_enc_conv", mic_enc_conv_, MIC_ENC_CONV_SIZE);
        resize_cache("mic_enc_tfa",  mic_enc_tfa_,  MIC_ENC_TFA_SIZE);
        resize_cache("deep_enc_tfa", deep_enc_tfa_, DEEP_ENC_TFA_SIZE);
        resize_cache("dec_conv",     dec_conv_,     DEC_CONV_SIZE);
        resize_cache("dec_tfa",      dec_tfa_,      DEC_TFA_SIZE);
        resize_cache("inter",        inter_,        INTER_SIZE);
        resize_cache("delay_buf",    delay_buf_,    DELAY_BUF_SIZE);

        // prev caches are 4D: (1,1,1,256) — detect from ONNX
        auto get_prev_size = [&](const std::string& name, size_t fallback) -> size_t {
            for (size_t i = 0; i < input_names_.size(); ++i) {
                if (input_names_[i] == name) {
                    auto shape = session_->GetInputTypeInfo(i)
                                     .GetTensorTypeAndShapeInfo().GetShape();
                    size_t total = 1;
                    for (auto d : shape) { if (d <= 0) return fallback; total *= (size_t)d; }
                    return total;
                }
            }
            return fallback;
        };

        size_t prev_sz = get_prev_size("res_prev1", PREV_SIZE);
        res_prev1_.resize(prev_sz, 0.0f);
        res_prev2_.resize(prev_sz, 0.0f);
        mic_prev1_.resize(prev_sz, 0.0f);
        mic_prev2_.resize(prev_sz, 0.0f);

        // Store input shapes for tensor creation
        input_shapes_.clear();
        for (size_t i = 0; i < input_names_.size(); ++i) {
            auto shape = session_->GetInputTypeInfo(i)
                             .GetTensorTypeAndShapeInfo().GetShape();
            input_shapes_.push_back(shape);
        }
    }

    ~AecProcessor() {
        if (fft_plan_)  { pffft_destroy_setup(fft_plan_); }
        if (fft_in_)    { pffft_aligned_free(fft_in_); }
        if (fft_out_)   { pffft_aligned_free(fft_out_); }
        if (ifft_out_)  { pffft_aligned_free(ifft_out_); }
    }

    /// Process 1024-sample chunks directly (matches AEC_HOP externally).
    void process_frame(const float* mic_1024, const float* far_1024, float* output_1024) {
        process_one_frame(mic_1024, far_1024);
        // process_one_frame appends AEC_HOP (1024) samples to out_acc_
        if (out_acc_.size() - out_acc_pos_ >= static_cast<size_t>(AEC_HOP)) {
            std::memcpy(output_1024, out_acc_.data() + out_acc_pos_, AEC_HOP * sizeof(float));
            out_acc_pos_ += AEC_HOP;
        } else {
            std::memset(output_1024, 0, AEC_HOP * sizeof(float));
        }
        // Drain consumed output
        if (out_acc_pos_ >= static_cast<size_t>(AEC_HOP) && out_acc_pos_ <= out_acc_.size()) {
            out_acc_.erase(out_acc_.begin(), out_acc_.begin() + out_acc_pos_);
            out_acc_pos_ = 0;
        }
    }

    /// pybind11-friendly wrapper
    std::vector<float> process_frame_py(const std::vector<float>& mic_vec, const std::vector<float>& far_vec) {
        std::vector<float> output(1024);
        process_frame(mic_vec.data(), far_vec.data(), output.data());
        return output;
    }

    void reset() {
        std::fill(res_enc_conv_.begin(), res_enc_conv_.end(), 0.0f);
        std::fill(res_enc_tfa_.begin(),  res_enc_tfa_.end(),  0.0f);
        std::fill(mic_enc_conv_.begin(), mic_enc_conv_.end(), 0.0f);
        std::fill(mic_enc_tfa_.begin(),  mic_enc_tfa_.end(),  0.0f);
        std::fill(deep_enc_tfa_.begin(), deep_enc_tfa_.end(), 0.0f);
        std::fill(dec_conv_.begin(),     dec_conv_.end(),     0.0f);
        std::fill(dec_tfa_.begin(),      dec_tfa_.end(),      0.0f);
        std::fill(inter_.begin(),        inter_.end(),        0.0f);
        std::fill(res_prev1_.begin(),    res_prev1_.end(),    0.0f);
        std::fill(res_prev2_.begin(),    res_prev2_.end(),    0.0f);
        std::fill(mic_prev1_.begin(),    mic_prev1_.end(),    0.0f);
        std::fill(mic_prev2_.begin(),    mic_prev2_.end(),    0.0f);
        std::fill(delay_buf_.begin(),    delay_buf_.end(),    0.0f);
        std::fill(mic_history_.begin(),  mic_history_.end(),  0.0f);
        std::fill(far_history_.begin(),  far_history_.end(),  0.0f);
        std::fill(ola_accumulator_.begin(), ola_accumulator_.end(), 0.0f);
        std::fill(window_sum_.begin(),   window_sum_.end(),   0.0f);
        out_acc_.clear();
        out_acc_pos_ = 0;
    }

private:
    /// Process one 1024-sample frame (internal)
    void process_one_frame(const float* mic_1024, const float* far_1024) {
        // ── 1. Update sliding history buffers: shift left by HOP, append new ──
        std::memmove(mic_history_.data(), mic_history_.data() + AEC_HOP,
                     (AEC_NFFT - AEC_HOP) * sizeof(float));
        std::memcpy(mic_history_.data() + AEC_NFFT - AEC_HOP, mic_1024, AEC_HOP * sizeof(float));

        std::memmove(far_history_.data(), far_history_.data() + AEC_HOP,
                     (AEC_NFFT - AEC_HOP) * sizeof(float));
        std::memcpy(far_history_.data() + AEC_NFFT - AEC_HOP, far_1024, AEC_HOP * sizeof(float));

        // ── 2. Mic STFT: window → FFT → planar (real, imag) ──
        compute_stft_frame(mic_history_.data(), mic_onnx_.data());

        // ── 3. Far STFT ──
        compute_stft_frame(far_history_.data(), far_onnx_.data());

        // ── 4. ONNX inference — build tensors by input name ──
        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);

        std::vector<Ort::Value> inputs;
        inputs.reserve(input_names_.size());

        for (size_t i = 0; i < input_names_.size(); ++i) {
            const auto& name = input_names_[i];
            auto shape = input_shapes_[i];  // copy

            if (name == "mic_frame") {
                // (1, 2, 1, 1025)
                std::vector<int64_t> s = {1, 2, 1, AEC_FREQ};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, mic_onnx_.data(), mic_onnx_.size(), s.data(), s.size()));
            } else if (name == "far_frame") {
                std::vector<int64_t> s = {1, 2, 1, AEC_FREQ};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, far_onnx_.data(), far_onnx_.size(), s.data(), s.size()));
            } else if (name == "res_enc_conv") {
                std::vector<int64_t> s = {1, (int64_t)res_enc_conv_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, res_enc_conv_.data(), res_enc_conv_.size(), s.data(), s.size()));
            } else if (name == "res_enc_tfa") {
                std::vector<int64_t> s = {1, (int64_t)res_enc_tfa_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, res_enc_tfa_.data(), res_enc_tfa_.size(), s.data(), s.size()));
            } else if (name == "mic_enc_conv") {
                std::vector<int64_t> s = {1, (int64_t)mic_enc_conv_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, mic_enc_conv_.data(), mic_enc_conv_.size(), s.data(), s.size()));
            } else if (name == "mic_enc_tfa") {
                std::vector<int64_t> s = {1, (int64_t)mic_enc_tfa_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, mic_enc_tfa_.data(), mic_enc_tfa_.size(), s.data(), s.size()));
            } else if (name == "deep_enc_conv") {
                // Zero-size tensor — ONNX may optimize away, but if present create it
                std::vector<int64_t> s = {1, 0};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, deep_enc_tfa_.data(), 0, s.data(), s.size()));
            } else if (name == "deep_enc_tfa") {
                std::vector<int64_t> s = {1, (int64_t)deep_enc_tfa_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, deep_enc_tfa_.data(), deep_enc_tfa_.size(), s.data(), s.size()));
            } else if (name == "dec_conv") {
                std::vector<int64_t> s = {1, (int64_t)dec_conv_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, dec_conv_.data(), dec_conv_.size(), s.data(), s.size()));
            } else if (name == "dec_tfa") {
                std::vector<int64_t> s = {1, (int64_t)dec_tfa_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, dec_tfa_.data(), dec_tfa_.size(), s.data(), s.size()));
            } else if (name == "inter") {
                std::vector<int64_t> s = {1, (int64_t)inter_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, inter_.data(), inter_.size(), s.data(), s.size()));
            } else if (name == "res_prev1") {
                std::vector<int64_t> s = {1, 1, 1, (int64_t)res_prev1_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, res_prev1_.data(), res_prev1_.size(), s.data(), s.size()));
            } else if (name == "res_prev2") {
                std::vector<int64_t> s = {1, 1, 1, (int64_t)res_prev2_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, res_prev2_.data(), res_prev2_.size(), s.data(), s.size()));
            } else if (name == "mic_prev1") {
                std::vector<int64_t> s = {1, 1, 1, (int64_t)mic_prev1_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, mic_prev1_.data(), mic_prev1_.size(), s.data(), s.size()));
            } else if (name == "mic_prev2") {
                std::vector<int64_t> s = {1, 1, 1, (int64_t)mic_prev2_.size()};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, mic_prev2_.data(), mic_prev2_.size(), s.data(), s.size()));
            } else if (name == "delay_buf") {
                // (1, 3, 52, 256)
                std::vector<int64_t> s = {1, 3, 52, 256};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, delay_buf_.data(), delay_buf_.size(), s.data(), s.size()));
            } else {
                throw std::runtime_error("AEC9: Unknown input tensor: " + name);
            }
        }

        std::vector<const char*> in_names(input_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i)
            in_names[i] = input_names_[i].c_str();
        std::vector<const char*> out_names(output_names_.size());
        for (size_t i = 0; i < output_names_.size(); ++i)
            out_names[i] = output_names_[i].c_str();

        auto outputs = session_->Run(Ort::RunOptions{nullptr},
                                     in_names.data(), inputs.data(), inputs.size(),
                                     out_names.data(), out_names.size());

        // ── 5. Update caches by output name ──
        for (size_t i = 0; i < output_names_.size(); ++i) {
            const auto& name = output_names_[i];
            float* data = outputs[i].GetTensorMutableData<float>();
            auto out_shape = outputs[i].GetTensorTypeAndShapeInfo().GetShape();
            size_t total = 1;
            for (auto d : out_shape) { if (d <= 0) { total = 0; break; } total *= (size_t)d; }

            if (name == "enhanced_frame") {
                // (1, 2, 1, 1025) — planar: [real_0..1024, imag_0..1024]
                // Convert to pffft format for iSTFT
                fft_out_[0] = data[0];                          // DC real
                fft_out_[1] = data[AEC_FREQ - 1];              // Nyquist real
                for (int k = 1; k < AEC_FREQ - 1; ++k) {
                    int pffft_idx = 2 + (k - 1) * 2;
                    fft_out_[pffft_idx]     = data[k];                 // real
                    fft_out_[pffft_idx + 1] = data[AEC_FREQ + k];     // imag
                }
            } else if (name == "res_enc_conv" || name == "res_enc_conv_o") {
                size_t n = std::min(total, res_enc_conv_.size());
                std::memcpy(res_enc_conv_.data(), data, n * sizeof(float));
            } else if (name == "res_enc_tfa" || name == "res_enc_tfa_o") {
                size_t n = std::min(total, res_enc_tfa_.size());
                std::memcpy(res_enc_tfa_.data(), data, n * sizeof(float));
            } else if (name == "mic_enc_conv" || name == "mic_enc_conv_o") {
                size_t n = std::min(total, mic_enc_conv_.size());
                std::memcpy(mic_enc_conv_.data(), data, n * sizeof(float));
            } else if (name == "mic_enc_tfa" || name == "mic_enc_tfa_o") {
                size_t n = std::min(total, mic_enc_tfa_.size());
                std::memcpy(mic_enc_tfa_.data(), data, n * sizeof(float));
            } else if (name == "deep_enc_conv_o") {
                // Zero-size — skip
            } else if (name == "deep_enc_tfa" || name == "deep_enc_tfa_o") {
                size_t n = std::min(total, deep_enc_tfa_.size());
                std::memcpy(deep_enc_tfa_.data(), data, n * sizeof(float));
            } else if (name == "dec_conv" || name == "dec_conv_o") {
                size_t n = std::min(total, dec_conv_.size());
                std::memcpy(dec_conv_.data(), data, n * sizeof(float));
            } else if (name == "dec_tfa" || name == "dec_tfa_o") {
                size_t n = std::min(total, dec_tfa_.size());
                std::memcpy(dec_tfa_.data(), data, n * sizeof(float));
            } else if (name == "inter" || name == "inter_o") {
                size_t n = std::min(total, inter_.size());
                std::memcpy(inter_.data(), data, n * sizeof(float));
            } else if (name == "res_prev1" || name == "res_prev1_o") {
                size_t n = std::min(total, res_prev1_.size());
                std::memcpy(res_prev1_.data(), data, n * sizeof(float));
            } else if (name == "res_prev2" || name == "res_prev2_o") {
                size_t n = std::min(total, res_prev2_.size());
                std::memcpy(res_prev2_.data(), data, n * sizeof(float));
            } else if (name == "mic_prev1" || name == "mic_prev1_o") {
                size_t n = std::min(total, mic_prev1_.size());
                std::memcpy(mic_prev1_.data(), data, n * sizeof(float));
            } else if (name == "mic_prev2" || name == "mic_prev2_o") {
                size_t n = std::min(total, mic_prev2_.size());
                std::memcpy(mic_prev2_.data(), data, n * sizeof(float));
            } else if (name == "delay_buf" || name == "delay_buf_o") {
                size_t n = std::min(total, delay_buf_.size());
                std::memcpy(delay_buf_.data(), data, n * sizeof(float));
            }
        }

        // ── 6. iSTFT: pffft format → IFFT → OLA ──
        pffft_transform_ordered(fft_plan_, fft_out_, ifft_out_, nullptr, PFFFT_BACKWARD);

        float scale = 1.0f / AEC_NFFT;
        for (int i = 0; i < AEC_NFFT; ++i) {
            ifft_out_[i] *= scale * window_[i];
        }

        for (int i = 0; i < AEC_NFFT; ++i) {
            ola_accumulator_[i] += ifft_out_[i];
        }

        for (int i = 0; i < AEC_NFFT; ++i) {
            window_sum_[i] += window_[i] * window_[i];
        }

        // Output AEC_HOP samples with normalization
        for (int i = 0; i < AEC_HOP; ++i) {
            float norm = window_sum_[i];
            float val = (norm > 1e-6f) ? (ola_accumulator_[i] / norm) : ola_accumulator_[i];
            out_acc_.push_back(val);
        }

        // Shift OLA buffers by AEC_HOP
        for (int i = 0; i < AEC_NFFT - AEC_HOP; ++i) {
            ola_accumulator_[i] = ola_accumulator_[i + AEC_HOP];
            window_sum_[i] = window_sum_[i + AEC_HOP];
        }
        for (int i = AEC_NFFT - AEC_HOP; i < AEC_NFFT; ++i) {
            ola_accumulator_[i] = 0.0f;
            window_sum_[i] = 0.0f;
        }
    }

    /// Apply sqrt-Hann window, compute pffft ForwardReal, convert to planar (real, imag)
    void compute_stft_frame(const float* input_nfft, float* onnx_spec) {
        for (int i = 0; i < AEC_NFFT; ++i) {
            fft_in_[i] = input_nfft[i] * window_[i];
        }

        pffft_transform_ordered(fft_plan_, fft_in_, fft_out_, nullptr, PFFFT_FORWARD);

        // DC (k=0)
        onnx_spec[0] = fft_out_[0];
        onnx_spec[AEC_FREQ] = 0.0f;

        // Bins 1..AEC_FREQ-2
        for (int k = 1; k < AEC_FREQ - 1; ++k) {
            int pffft_idx = 2 + (k - 1) * 2;
            onnx_spec[k]            = fft_out_[pffft_idx];
            onnx_spec[AEC_FREQ + k] = fft_out_[pffft_idx + 1];
        }

        // Nyquist (k = AEC_FREQ-1)
        onnx_spec[AEC_FREQ - 1]            = fft_out_[1];
        onnx_spec[AEC_FREQ + AEC_FREQ - 1] = 0.0f;
    }

    std::shared_ptr<Ort::Env> env_;
    std::shared_ptr<Ort::Session> session_;
    std::vector<std::string> input_names_;
    std::vector<std::string> output_names_;
    std::vector<std::vector<int64_t>> input_shapes_;

    // STFT
    std::vector<float> window_;
    float *fft_in_ = nullptr, *fft_out_ = nullptr, *ifft_out_ = nullptr;
    PFFFT_Setup* fft_plan_ = nullptr;

    // Sliding history buffers (AEC_NFFT = 2048 samples each)
    std::vector<float> mic_history_;
    std::vector<float> far_history_;

    // Overlap-add accumulators for iSTFT
    std::vector<float> ola_accumulator_;
    std::vector<float> window_sum_;

    // ONNX I/O buffers: planar (real, imag), flat 2050 floats
    std::vector<float> mic_onnx_;
    std::vector<float> far_onnx_;

    // ── aec9 caches ──
    std::vector<float> res_enc_conv_;   // ~108544
    std::vector<float> res_enc_tfa_;    // 248
    std::vector<float> mic_enc_conv_;   // ~108544
    std::vector<float> mic_enc_tfa_;    // 248
    std::vector<float> deep_enc_tfa_;   // 336
    std::vector<float> dec_conv_;       // ~10752
    std::vector<float> dec_tfa_;        // 496
    std::vector<float> inter_;          // ~6144
    std::vector<float> res_prev1_;      // 256 (flat)
    std::vector<float> res_prev2_;      // 256
    std::vector<float> mic_prev1_;      // 256
    std::vector<float> mic_prev2_;      // 256
    std::vector<float> delay_buf_;      // 39936

    // ── Output buffer ──
    std::vector<float> out_acc_;
    size_t out_acc_pos_;
};


// ============================================================================
// StftProcessor — unified FFT/IFFT/OLA (2048-pt, 1024-hop, 48kHz)
// Produces/consumes planar spectrum: [r0..r1024, i0..i1024] = 2050 floats.
// ============================================================================

class StftProcessor {
public:
    static const int NFFT = 2048, HOP = 1024, FREQ = 1025, SPEC_FLOATS = 2050;

    StftProcessor()
        : input_history_(NFFT - HOP, 0.0f), ola_acc_(NFFT, 0.0f), win_sum_(NFFT, 0.0f), primed_(false) {
        window_ = create_hann_window(NFFT);
        for (auto& w : window_) w *= w;
        fft_plan_ = pffft_new_setup(NFFT, PFFFT_REAL);
        fft_in_   = (float*)pffft_aligned_malloc(NFFT * sizeof(float));
        fft_out_  = (float*)pffft_aligned_malloc(NFFT * sizeof(float));
        ifft_out_ = (float*)pffft_aligned_malloc(NFFT * sizeof(float));
    }
    ~StftProcessor() {
        if (fft_plan_) pffft_destroy_setup(fft_plan_);
        if (fft_in_)   pffft_aligned_free(fft_in_);
        if (fft_out_)  pffft_aligned_free(fft_out_);
        if (ifft_out_) pffft_aligned_free(ifft_out_);
    }

    void forward(const float* in, float* spec_planar) {
        size_t prev = NFFT - HOP;
        std::memcpy(fft_in_, input_history_.data(), prev * sizeof(float));
        std::memcpy(fft_in_ + prev, in, HOP * sizeof(float));
        std::memmove(input_history_.data(), input_history_.data() + HOP, (prev - HOP) * sizeof(float));
        std::memcpy(input_history_.data() + prev - HOP, in, HOP * sizeof(float));
        for (int i = 0; i < NFFT; ++i) fft_in_[i] *= window_[i];
        pffft_transform_ordered(fft_plan_, fft_in_, fft_out_, nullptr, PFFFT_FORWARD);
        float* rp = spec_planar, *ip = spec_planar + FREQ;
        rp[0] = fft_out_[0]; ip[0] = 0.0f;
        rp[FREQ - 1] = fft_out_[1]; ip[FREQ - 1] = 0.0f;
        for (int k = 1; k < FREQ - 1; ++k) {
            int p = 2 + (k - 1) * 2;
            rp[k] = fft_out_[p]; ip[k] = fft_out_[p + 1];
        }
    }

    void backward(const float* spec_planar, float* out) {
        fft_out_[0] = spec_planar[0]; fft_out_[1] = spec_planar[FREQ - 1];
        for (int k = 1; k < FREQ - 1; ++k) {
            int p = 2 + (k - 1) * 2;
            fft_out_[p] = spec_planar[k]; fft_out_[p + 1] = spec_planar[FREQ + k];
        }
        pffft_transform_ordered(fft_plan_, fft_out_, ifft_out_, nullptr, PFFFT_BACKWARD);
        float s = 1.0f / NFFT;
        for (int i = 0; i < NFFT; ++i) { ifft_out_[i] *= s * window_[i]; ola_acc_[i] += ifft_out_[i]; win_sum_[i] += window_[i] * window_[i]; }
        if (!primed_) { primed_ = true; std::memset(out, 0, HOP * sizeof(float)); }
        else { for (int i = 0; i < HOP; ++i) { float n = win_sum_[i]; out[i] = (n > 1e-6f) ? (ola_acc_[i] / n) : ola_acc_[i]; } }
        for (int i = 0; i < NFFT - HOP; ++i) { ola_acc_[i] = ola_acc_[i + HOP]; win_sum_[i] = win_sum_[i + HOP]; }
        for (int i = NFFT - HOP; i < NFFT; ++i) { ola_acc_[i] = 0.0f; win_sum_[i] = 0.0f; }
    }

    void reset() { std::fill(input_history_.begin(), input_history_.end(), 0.0f);
        std::fill(ola_acc_.begin(), ola_acc_.end(), 0.0f);
        std::fill(win_sum_.begin(), win_sum_.end(), 0.0f); primed_ = false; }

    static void planar_to_interleaved(const float* p, float* il) {
        for (int k = 0; k < FREQ; ++k) { il[k*2] = p[k]; il[k*2+1] = p[FREQ + k]; }
    }
    static void interleaved_to_planar(const float* il, float* p) {
        for (int k = 0; k < FREQ; ++k) { p[k] = il[k*2]; p[FREQ + k] = il[k*2+1]; }
    }

private:
    PFFFT_Setup* fft_plan_ = nullptr;
    float *fft_in_ = nullptr, *fft_out_ = nullptr, *ifft_out_ = nullptr;
    std::vector<float> window_, input_history_, ola_acc_, win_sum_;
    bool primed_;
};


// ============================================================================
// Denoise Processor — purevox9 (2048 FFT + Band256, Single STFT)
//   ONNX inputs:  spec [1,1025,1,2], enc_c [1,77106], dec_c [1,53862],
//                 tfa_c [1,1056], inter_c [1,1024]
//   ONNX outputs: enhanced_spec [1,1025,1,2] + 4 updated caches
//   Cache sizes auto-detected from ONNX model at init.
//   External interface: 1024-sample chunks (DENOISE_HOP=1024).
// ============================================================================

class DenoiseProcessor {
public:
    static const int DENOISE_NFFT = 2048;
    static const int DENOISE_HOP = 1024;
    static const int DENOISE_FREQ = 1025;  // NFFT/2 + 1
    static const int DENOISE_SPEC_SIZE = DENOISE_FREQ * 2;  // 2050 (interleaved [r,i] per bin)

    // Default cache sizes — v9 FreqStem k=7 causal
    // enc_c: FS_enc(1*6*1027 + 16*6*515 = 55602) + Enc_dconv(21504) = 77106
    // dec_c: FS_dec(1*6*257 + 16*6*513 = 50790) + Dec_dconv(3072) = 53862
    static const int DENOISE_ENC_C_SIZE   = 77106;
    static const int DENOISE_DEC_C_SIZE   = 53862;
    static const int DENOISE_TFA_C_SIZE   = 1056;
    static const int DENOISE_INTER_C_SIZE = 1024;

    DenoiseProcessor(const std::string& model_path)
        : input_history_(DENOISE_NFFT - DENOISE_HOP, 0.0f),
          ola_accumulator_(DENOISE_NFFT, 0.0f),
          window_sum_(DENOISE_NFFT, 0.0f)
    {
        window_ = create_hann_window(DENOISE_NFFT);

        // Initialize ONNX
        Ort::SessionOptions session_options;
        session_options.SetIntraOpNumThreads(1);
        session_options.SetInterOpNumThreads(1);
        session_options.SetExecutionMode(ORT_SEQUENTIAL);
        session_options.SetGraphOptimizationLevel(ORT_ENABLE_BASIC);
        env_ = std::make_shared<Ort::Env>(ORT_LOGGING_LEVEL_WARNING, "DenoiseProcessor");

#ifdef _WIN32
        int wide_size = MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, nullptr, 0);
        std::wstring wide_path(wide_size, 0);
        MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, &wide_path[0], wide_size);
        Ort::Session session(*env_, wide_path.c_str(), session_options);
#else
        Ort::Session session(*env_, model_path.c_str(), session_options);
#endif
        session_ = std::make_shared<Ort::Session>(std::move(session));

        input_names_ = session_->GetInputNames();
        output_names_ = session_->GetOutputNames();

        // Store input shapes for dynamic tensor building
        for (size_t i = 0; i < input_names_.size(); ++i) {
            auto type_info = session_->GetInputTypeInfo(i);
            auto tensor_info = type_info.GetTensorTypeAndShapeInfo();
            input_shapes_.push_back(tensor_info.GetShape());
        }

        // Auto-detect cache sizes from ONNX
        auto get_cache_size = [&](const std::string& name, size_t default_size) -> size_t {
            for (size_t i = 0; i < input_names_.size(); ++i) {
                if (input_names_[i] == name) {
                    auto shape = input_shapes_[i];
                    size_t total = 1;
                    for (auto d : shape) {
                        if (d <= 0) { total = default_size; break; }
                        total *= static_cast<size_t>(d);
                    }
                    return total;
                }
            }
            return default_size;
        };

        enc_c_size_ = static_cast<int>(get_cache_size("enc_c", DENOISE_ENC_C_SIZE));
        dec_c_size_ = static_cast<int>(get_cache_size("dec_c", DENOISE_DEC_C_SIZE));
        tfa_c_size_ = static_cast<int>(get_cache_size("tfa_c", DENOISE_TFA_C_SIZE));
        inter_c_size_ = static_cast<int>(get_cache_size("inter_c", DENOISE_INTER_C_SIZE));

        enc_c_.resize(enc_c_size_, 0.0f);
        dec_c_.resize(dec_c_size_, 0.0f);
        tfa_c_.resize(tfa_c_size_, 0.0f);
        inter_c_.resize(inter_c_size_, 0.0f);

        // FFT resources
        fft_plan_ = pffft_new_setup(DENOISE_NFFT, PFFFT_REAL);
        fft_in_ = static_cast<float*>(pffft_aligned_malloc(DENOISE_NFFT * sizeof(float)));
        fft_out_ = static_cast<float*>(pffft_aligned_malloc(DENOISE_NFFT * sizeof(float)));
        ifft_out_ = static_cast<float*>(pffft_aligned_malloc(DENOISE_NFFT * sizeof(float)));

        // Model input spec buffer: (1, 1025, 1, 2) = 2050 floats, interleaved [r,i] per bin
        model_spec_.resize(DENOISE_SPEC_SIZE, 0.0f);

        // Internal accumulation buffers
        acc_output_.reserve(DENOISE_HOP * 3);

        // Pre-warm: feed 3 silent 1024-sample chunks to prime model caches
        for (int i = 0; i < 3; i++) {
            std::vector<float> silent(1024, 0.0f);
            std::vector<float> dummy(1024);
            process_chunk(silent.data(), dummy.data());
        }
        acc_output_.clear();  // discard warmup output
    }

    ~DenoiseProcessor() {
        if (fft_plan_) { pffft_destroy_setup(fft_plan_); }
        if (fft_in_)   { pffft_aligned_free(fft_in_); }
        if (fft_out_)  { pffft_aligned_free(fft_out_); }
        if (ifft_out_) { pffft_aligned_free(ifft_out_); }
    }

    // Process 1024-sample chunks directly (matches DENOISE_HOP externally).
    void process_chunk(const float* input_1024, float* output_1024) {
        process_one_frame(input_1024);
        // process_one_frame always appends DENOISE_HOP (1024) samples to acc_output_
        if (acc_output_.size() >= static_cast<size_t>(DENOISE_HOP)) {
            std::memcpy(output_1024, acc_output_.data(), DENOISE_HOP * sizeof(float));
            acc_output_.erase(acc_output_.begin(), acc_output_.begin() + DENOISE_HOP);
        } else {
            std::memset(output_1024, 0, DENOISE_HOP * sizeof(float));
        }
    }

private:
    // Process one 1024-sample frame through the v7 ONNX model (real workhorse)
    void process_one_frame(const float* input_1024) {
        // ── 1. Build frame: 1024-sample history + 1024 new samples ──
        size_t prev_size = DENOISE_NFFT - DENOISE_HOP;
        std::memcpy(fft_in_, input_history_.data(), prev_size * sizeof(float));
        std::memcpy(fft_in_ + prev_size, input_1024, DENOISE_HOP * sizeof(float));

        std::memmove(input_history_.data(), input_history_.data() + DENOISE_HOP,
                     (prev_size - DENOISE_HOP) * sizeof(float));
        std::memcpy(input_history_.data() + prev_size - DENOISE_HOP, input_1024, DENOISE_HOP * sizeof(float));

        // ── 2. Window + FFT (full band, 1025 bins) ──
        for (int i = 0; i < DENOISE_NFFT; ++i) {
            fft_in_[i] *= window_[i];
        }
        pffft_transform_ordered(fft_plan_, fft_in_, fft_out_, nullptr, PFFFT_FORWARD);

        // ── 3. PFFFT ordered → model input interleaved [r,i] (1, 1025, 1, 2) ──
        // PFFFT ordered: [DC_real, Nyquist_real, bin1_real, bin1_imag, bin2_real, bin2_imag, ...]
        model_spec_[0] = fft_out_[0];           // DC real
        model_spec_[1] = 0.0f;                  // DC imag
        model_spec_[DENOISE_SPEC_SIZE - 2] = fft_out_[1];  // Nyquist real
        model_spec_[DENOISE_SPEC_SIZE - 1] = 0.0f;    // Nyquist imag
        for (int k = 1; k < DENOISE_FREQ - 1; ++k) {
            int pffft_idx = 2 + (k - 1) * 2;
            model_spec_[k * 2]     = fft_out_[pffft_idx];      // real
            model_spec_[k * 2 + 1] = fft_out_[pffft_idx + 1];  // imag
        }

        // ── 4. ONNX inference (5 inputs, dynamic from model metadata) ──
        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);

        std::vector<int64_t> spec_shape = {1, DENOISE_FREQ, 1, 2};
        std::vector<Ort::Value> inputs;
        inputs.reserve(input_names_.size());

        for (size_t i = 0; i < input_names_.size(); ++i) {
            const auto& name = input_names_[i];
            auto shape = input_shapes_[i];

            if (name == "spec") {
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, model_spec_.data(), model_spec_.size(),
                    spec_shape.data(), spec_shape.size()));
            } else if (name == "enc_c") {
                std::vector<int64_t> s = {1, enc_c_size_};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, enc_c_.data(), enc_c_.size(),
                    s.data(), s.size()));
            } else if (name == "dec_c") {
                std::vector<int64_t> s = {1, dec_c_size_};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, dec_c_.data(), dec_c_.size(),
                    s.data(), s.size()));
            } else if (name == "tfa_c") {
                std::vector<int64_t> s = {1, tfa_c_size_};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, tfa_c_.data(), tfa_c_.size(),
                    s.data(), s.size()));
            } else if (name == "inter_c") {
                std::vector<int64_t> s = {1, inter_c_size_};
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, inter_c_.data(), inter_c_.size(),
                    s.data(), s.size()));
            }
        }

        std::vector<const char*> in_names(input_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i)
            in_names[i] = input_names_[i].c_str();
        std::vector<const char*> out_names(output_names_.size());
        for (size_t i = 0; i < output_names_.size(); ++i)
            out_names[i] = output_names_[i].c_str();

        auto outputs = session_->Run(Ort::RunOptions{nullptr},
                                     in_names.data(), inputs.data(), inputs.size(),
                                     out_names.data(), out_names.size());

        // ── 5. Update caches + read enhanced spec from outputs ──
        for (size_t i = 0; i < output_names_.size(); ++i) {
            const auto& name = output_names_[i];
            float* data = outputs[i].GetTensorMutableData<float>();
            auto out_shape = outputs[i].GetTensorTypeAndShapeInfo().GetShape();
            size_t total = 1;
            for (auto d : out_shape) {
                if (d <= 0) { total = 0; break; }
                total *= static_cast<size_t>(d);
            }

            if (name == "enhanced_spec") {
                std::memcpy(model_spec_.data(), data, model_spec_.size() * sizeof(float));
            } else if (name == "enc_c_out") {
                size_t n = (total > 0 && total <= static_cast<size_t>(enc_c_size_)) ? total : static_cast<size_t>(enc_c_size_);
                std::memcpy(enc_c_.data(), data, n * sizeof(float));
            } else if (name == "dec_c_out") {
                size_t n = (total > 0 && total <= static_cast<size_t>(dec_c_size_)) ? total : static_cast<size_t>(dec_c_size_);
                std::memcpy(dec_c_.data(), data, n * sizeof(float));
            } else if (name == "tfa_c_out") {
                size_t n = (total > 0 && total <= static_cast<size_t>(tfa_c_size_)) ? total : static_cast<size_t>(tfa_c_size_);
                std::memcpy(tfa_c_.data(), data, n * sizeof(float));
            } else if (name == "inter_c_out") {
                size_t n = (total > 0 && total <= static_cast<size_t>(inter_c_size_)) ? total : static_cast<size_t>(inter_c_size_);
                std::memcpy(inter_c_.data(), data, n * sizeof(float));
            }
        }

        // ── 6. Model output interleaved → PFFFT packed format → IFFT ──
        fft_out_[0] = model_spec_[0];                    // DC real
        fft_out_[1] = model_spec_[DENOISE_SPEC_SIZE - 2];     // Nyquist real
        for (int k = 1; k < DENOISE_FREQ - 1; ++k) {
            int pffft_idx = 2 + (k - 1) * 2;
            fft_out_[pffft_idx]     = model_spec_[k * 2];        // real
            fft_out_[pffft_idx + 1] = model_spec_[k * 2 + 1];    // imag
        }

        pffft_transform_ordered(fft_plan_, fft_out_, ifft_out_, nullptr, PFFFT_BACKWARD);

        // ── 7. Synthesis window + overlap-add (matches torch.istft) ──
        float scale = 1.0f / DENOISE_NFFT;
        for (int i = 0; i < DENOISE_NFFT; ++i) {
            ifft_out_[i] *= scale * window_[i];
        }
        for (int i = 0; i < DENOISE_NFFT; ++i) {
            ola_accumulator_[i] += ifft_out_[i];
        }
        for (int i = 0; i < DENOISE_NFFT; ++i) {
            window_sum_[i] += window_[i] * window_[i];
        }

        for (int i = 0; i < DENOISE_HOP; ++i) {
            float norm = window_sum_[i];
            float val = (norm > 1e-6f) ? (ola_accumulator_[i] / norm) : ola_accumulator_[i];
            acc_output_.push_back(val);
        }

        // Shift OLA buffers by HOP
        for (int i = 0; i < DENOISE_NFFT - DENOISE_HOP; ++i) {
            ola_accumulator_[i] = ola_accumulator_[i + DENOISE_HOP];
            window_sum_[i] = window_sum_[i + DENOISE_HOP];
        }
        for (int i = DENOISE_NFFT - DENOISE_HOP; i < DENOISE_NFFT; ++i) {
            ola_accumulator_[i] = 0.0f;
            window_sum_[i] = 0.0f;
        }
    }

public:
    // Process frame through ONNX only — outputs interleaved spectrum (2050 floats).
    // Does FFT + ONNX inference, skips IFFT+OLA. Caller is responsible for IFFT.
    void process_spec_only(const float* input_1024, float* spec_out) {
        // ── FFT (same as process_one_frame) ──
        size_t prev_size = DENOISE_NFFT - DENOISE_HOP;
        std::memcpy(fft_in_, input_history_.data(), prev_size * sizeof(float));
        std::memcpy(fft_in_ + prev_size, input_1024, DENOISE_HOP * sizeof(float));
        std::memmove(input_history_.data(), input_history_.data() + DENOISE_HOP,
                     (prev_size - DENOISE_HOP) * sizeof(float));
        std::memcpy(input_history_.data() + prev_size - DENOISE_HOP, input_1024, DENOISE_HOP * sizeof(float));

        for (int i = 0; i < DENOISE_NFFT; ++i) fft_in_[i] *= window_[i];
        pffft_transform_ordered(fft_plan_, fft_in_, fft_out_, nullptr, PFFFT_FORWARD);

        model_spec_[0] = fft_out_[0]; model_spec_[1] = 0.0f;
        model_spec_[DENOISE_SPEC_SIZE - 2] = fft_out_[1];
        model_spec_[DENOISE_SPEC_SIZE - 1] = 0.0f;
        for (int k = 1; k < DENOISE_FREQ - 1; ++k) {
            int p = 2 + (k - 1) * 2;
            model_spec_[k * 2]     = fft_out_[p];
            model_spec_[k * 2 + 1] = fft_out_[p + 1];
        }

        // ── ONNX inference ──
        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);
        std::vector<int64_t> spec_shape = {1, DENOISE_FREQ, 1, 2};
        std::vector<Ort::Value> inputs;
        inputs.reserve(input_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i) {
            const auto& name = input_names_[i];
            if (name == "spec")
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, model_spec_.data(),
                    DENOISE_SPEC_SIZE, spec_shape.data(), spec_shape.size()));
            else if (name == "enc_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(enc_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, enc_c_.data(),
                    enc_c_.size(), s.data(), s.size()));
            } else if (name == "dec_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(dec_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, dec_c_.data(),
                    dec_c_.size(), s.data(), s.size()));
            } else if (name == "tfa_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(tfa_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, tfa_c_.data(),
                    tfa_c_.size(), s.data(), s.size()));
            } else if (name == "inter_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(inter_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, inter_c_.data(),
                    inter_c_.size(), s.data(), s.size()));
            }
        }
        std::vector<const char*> in_names(input_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i) in_names[i] = input_names_[i].c_str();
        std::vector<const char*> out_names(output_names_.size());
        for (size_t i = 0; i < output_names_.size(); ++i) out_names[i] = output_names_[i].c_str();
        auto outputs = session_->Run(Ort::RunOptions{nullptr},
                                     in_names.data(), inputs.data(), inputs.size(),
                                     out_names.data(), out_names.size());

        // Read enhanced_spec, update state caches
        for (size_t i = 0; i < output_names_.size(); ++i) {
            const auto& name = output_names_[i];
            float* data = outputs[i].GetTensorMutableData<float>();
            auto sh = outputs[i].GetTensorTypeAndShapeInfo().GetShape();
            size_t total = 1;
            for (auto d : sh) { if (d <= 0) { total = 0; break; } total *= static_cast<size_t>(d); }
            if (name == "enhanced_spec")
                std::memcpy(model_spec_.data(), data, model_spec_.size() * sizeof(float));
            else if (name == "enc_c_out") {
                size_t n = std::min(total, static_cast<size_t>(enc_c_size_));
                std::memcpy(enc_c_.data(), data, n * sizeof(float));
            } else if (name == "dec_c_out") {
                size_t n = std::min(total, static_cast<size_t>(dec_c_size_));
                std::memcpy(dec_c_.data(), data, n * sizeof(float));
            } else if (name == "tfa_c_out") {
                size_t n = std::min(total, static_cast<size_t>(tfa_c_size_));
                std::memcpy(tfa_c_.data(), data, n * sizeof(float));
            } else if (name == "inter_c_out") {
                size_t n = std::min(total, static_cast<size_t>(inter_c_size_));
                std::memcpy(inter_c_.data(), data, n * sizeof(float));
            }
        }

        // Copy enhanced spectrum to caller (interleaved format)
        std::memcpy(spec_out, model_spec_.data(), DENOISE_SPEC_SIZE * sizeof(float));
    }

    /// Pure frequency-domain ONNX: input interleaved spectrum → ONNX → output interleaved spectrum.
    /// No FFT/IFFT. Used in StftProcessor-based chaining.
    void process_spec_freq(const float* spec_in, float* spec_out) {
        std::memcpy(model_spec_.data(), spec_in, DENOISE_SPEC_SIZE * sizeof(float));
        // ── ONNX inference (same as process_spec_only tail) ──
        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);
        std::vector<int64_t> spec_shape = {1, DENOISE_FREQ, 1, 2};
        std::vector<Ort::Value> inputs;
        inputs.reserve(input_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i) {
            const auto& name = input_names_[i];
            if (name == "spec")
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, model_spec_.data(),
                    DENOISE_SPEC_SIZE, spec_shape.data(), spec_shape.size()));
            else if (name == "enc_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(enc_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, enc_c_.data(), enc_c_.size(), s.data(), s.size()));
            } else if (name == "dec_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(dec_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, dec_c_.data(), dec_c_.size(), s.data(), s.size()));
            } else if (name == "tfa_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(tfa_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, tfa_c_.data(), tfa_c_.size(), s.data(), s.size()));
            } else if (name == "inter_c") {
                std::vector<int64_t> s = {1, static_cast<int64_t>(inter_c_size_)};
                inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, inter_c_.data(), inter_c_.size(), s.data(), s.size()));
            }
        }
        std::vector<const char*> in_names(input_names_.size()), out_names(output_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i) in_names[i] = input_names_[i].c_str();
        for (size_t i = 0; i < output_names_.size(); ++i) out_names[i] = output_names_[i].c_str();
        auto outputs = session_->Run(Ort::RunOptions{nullptr}, in_names.data(), inputs.data(), inputs.size(), out_names.data(), out_names.size());
        for (size_t i = 0; i < output_names_.size(); ++i) {
            const auto& name = output_names_[i];
            float* data = outputs[i].GetTensorMutableData<float>();
            auto sh = outputs[i].GetTensorTypeAndShapeInfo().GetShape();
            size_t total = 1; for (auto d : sh) { if (d <= 0) { total = 0; break; } total *= (size_t)d; }
            if (name == "enhanced_spec") std::memcpy(model_spec_.data(), data, model_spec_.size() * sizeof(float));
            else if (name == "enc_c_out") { size_t n = std::min(total, (size_t)enc_c_size_); std::memcpy(enc_c_.data(), data, n * sizeof(float)); }
            else if (name == "dec_c_out") { size_t n = std::min(total, (size_t)dec_c_size_); std::memcpy(dec_c_.data(), data, n * sizeof(float)); }
            else if (name == "tfa_c_out") { size_t n = std::min(total, (size_t)tfa_c_size_); std::memcpy(tfa_c_.data(), data, n * sizeof(float)); }
            else if (name == "inter_c_out") { size_t n = std::min(total, (size_t)inter_c_size_); std::memcpy(inter_c_.data(), data, n * sizeof(float)); }
        }
        std::memcpy(spec_out, model_spec_.data(), DENOISE_SPEC_SIZE * sizeof(float));
    }


    void reset() {
        std::fill(enc_c_.begin(), enc_c_.end(), 0.0f);
        std::fill(dec_c_.begin(), dec_c_.end(), 0.0f);
        std::fill(tfa_c_.begin(), tfa_c_.end(), 0.0f);
        std::fill(inter_c_.begin(), inter_c_.end(), 0.0f);
        std::fill(input_history_.begin(), input_history_.end(), 0.0f);
        std::fill(ola_accumulator_.begin(), ola_accumulator_.end(), 0.0f);
        std::fill(window_sum_.begin(), window_sum_.end(), 0.0f);
        std::fill(model_spec_.begin(), model_spec_.end(), 0.0f);
        acc_output_.clear();
    }

private:
    std::shared_ptr<Ort::Env> env_;
    std::shared_ptr<Ort::Session> session_;
    std::vector<std::string> input_names_;
    std::vector<std::string> output_names_;
    std::vector<std::vector<int64_t>> input_shapes_;

    // FFT
    PFFFT_Setup* fft_plan_ = nullptr;
    float* fft_in_ = nullptr;
    float* fft_out_ = nullptr;
    float* ifft_out_ = nullptr;
    std::vector<float> window_;

    // Caches (auto-detected from ONNX)
    std::vector<float> enc_c_;
    std::vector<float> dec_c_;
    std::vector<float> tfa_c_;
    std::vector<float> inter_c_;
    int enc_c_size_ = DENOISE_ENC_C_SIZE;
    int dec_c_size_ = DENOISE_DEC_C_SIZE;
    int tfa_c_size_ = DENOISE_TFA_C_SIZE;
    int inter_c_size_ = DENOISE_INTER_C_SIZE;

    // OLA state
    std::vector<float> input_history_;
    std::vector<float> ola_accumulator_;
    std::vector<float> window_sum_;

    // Model input/output buffer (1, 1025, 1, 2) interleaved [r,i] per bin = 2050 floats
    std::vector<float> model_spec_;

    // Internal 1024-sample accumulation buffer
    std::vector<float> acc_output_;
};

// ============================================================================
// Tse Processor — streaming ONNX TSE (2048 FFT, 1024 HOP, 48kHz, flat cache)
//   ONNX: spec_frame [1,2,1,1025] + enr_spec [1,2,Te,1025] + cache_in [319040]
//         → enh_frame [1,2,1,1025] + cache_out [319040]
//   External interface: 1024-sample input/output + set_reference for enrollment
// ============================================================================
class TseProcessor {
public:
    static const int TSE_NFFT = 2048;
    static const int TSE_HOP  = 1024;
    static const int TSE_FREQ = 1025;
    static const int TSE_SPEC_FLOATS = 2 * TSE_FREQ;  // 2050 (real+imag per freq bin)
    // tse15: enrollment = raw STFT (1, 2, Te, 1025), 2 channels × 1025 freqs = 2050 floats/frame
    static const int TSE_ENR_CH = 2;
    static const int TSE_ENR_FLOATS = TSE_ENR_CH * TSE_FREQ;  // 2050
    static const int BUF_SIZE = TSE_HOP * 8;  // 环形缓冲容量

    static constexpr int GRU_LAYERS = 3;
    static constexpr int GRU_HIDDEN = 256;
    static constexpr int ENC_C0_CH = 64;

    explicit TseProcessor(const std::string& model_path)
        : input_history_(TSE_NFFT - TSE_HOP, 0.0f),
          ola_accumulator_(TSE_NFFT, 0.0f),
          window_sum_(TSE_NFFT, 0.0f),
          primed_(false),
          frame_count_(0),
          debug_dump_(false),
          cache_(CACHE_TOTAL, 0.0f),
          spec_buf_(TSE_SPEC_FLOATS, 0.0f),
          enr_buf_(TSE_ENR_FLOATS, 0.0f),
          input_buf_(BUF_SIZE),
          output_buf_(BUF_SIZE)
    {
        // Plain Hann window matching Python torch.hann_window(2048).
        // Analysis window = synthesis window = Hann.
        // NOLA normalization: OLA / sum(window^2) corrects for non-constant COLA.
        window_ = create_hann_window(TSE_NFFT);
        // Square to get plain Hann: create_hann_window returns sqrt-Hann
        for (size_t i = 0; i < window_.size(); ++i)
            window_[i] = window_[i] * window_[i];

        Ort::SessionOptions session_options;
        session_options.SetIntraOpNumThreads(1);
        session_options.SetInterOpNumThreads(1);
        session_options.SetExecutionMode(ORT_SEQUENTIAL);
        session_options.SetGraphOptimizationLevel(ORT_ENABLE_ALL);
        env_ = std::make_shared<Ort::Env>(ORT_LOGGING_LEVEL_WARNING, "TseProcessor");

#ifdef _WIN32
        int wide_size = MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, nullptr, 0);
        std::wstring wide_path(wide_size, 0);
        MultiByteToWideChar(CP_UTF8, 0, model_path.c_str(), -1, &wide_path[0], wide_size);
        Ort::Session session(*env_, wide_path.c_str(), session_options);
#else
        Ort::Session session(*env_, model_path.c_str(), session_options);
#endif
        session_ = std::make_shared<Ort::Session>(std::move(session));

        input_names_ = session_->GetInputNames();
        output_names_ = session_->GetOutputNames();

        // PFFFT
        fft_plan_ = pffft_new_setup(TSE_NFFT, PFFFT_REAL);
        fft_in_   = static_cast<float*>(pffft_aligned_malloc(TSE_NFFT * sizeof(float)));
        fft_out_  = static_cast<float*>(pffft_aligned_malloc(TSE_NFFT * sizeof(float)));
        ifft_out_ = static_cast<float*>(pffft_aligned_malloc(TSE_NFFT * sizeof(float)));
    }

    ~TseProcessor() {
        stop_worker();
        if (fft_plan_) { pffft_destroy_setup(fft_plan_); }
        if (fft_in_)   { pffft_aligned_free(fft_in_); }
        if (fft_out_)  { pffft_aligned_free(fft_out_); }
        if (ifft_out_) { pffft_aligned_free(ifft_out_); }
    }

    /// Set reference audio — compute STFT → store as enr_spec (1, 2, Te, 1025)
    void set_reference(const std::vector<float>& ref_audio) {
        size_t n = ref_audio.size();
        if (n < TSE_NFFT) return;

        // Number of frames with center=True: n / HOP + 1
        size_t n_frames = (n > 0) ? (n / TSE_HOP + 1) : 0;
        if (n_frames == 0) return;

        enr_spec_ref_frames_ = n_frames;
        enr_buf_.resize(n_frames * TSE_ENR_FLOATS, 0.0f);

        // Reflect pad (same as PyTorch center=True, pad_mode='reflect')
        size_t pad = TSE_NFFT / 2;
        std::vector<float> padded(n + pad * 2, 0.0f);
        for (size_t i = 0; i < pad && i + 1 < n; ++i)
            padded[pad - 1 - i] = ref_audio[i + 1];
        std::memcpy(padded.data() + pad, ref_audio.data(), n * sizeof(float));
        for (size_t i = 0; i < pad && i + 1 < n; ++i)
            padded[pad + n + i] = ref_audio[n - 2 - i];

        // Compute STFT for each frame, store as (1, 2, Te, 1025) planar
        // Layout: channel 0 = real (Te * 1025 floats), channel 1 = imag (Te * 1025 floats)
        float* real_ptr = enr_buf_.data();                          // channel 0: real
        float* imag_ptr = enr_buf_.data() + n_frames * TSE_FREQ;   // channel 1: imag

        for (size_t t = 0; t < n_frames; ++t) {
            size_t offset = t * TSE_HOP;
            for (int i = 0; i < TSE_NFFT; ++i)
                fft_in_[i] = padded[offset + i] * window_[i];
            pffft_transform_ordered(fft_plan_, fft_in_, fft_out_, nullptr, PFFFT_FORWARD);

            // PFFFT → planar real/imag
            real_ptr[t * TSE_FREQ + 0] = fft_out_[0];
            imag_ptr[t * TSE_FREQ + 0] = 0.0f;
            for (int k = 1; k < TSE_FREQ - 1; ++k) {
                int pidx = 2 + (k - 1) * 2;
                real_ptr[t * TSE_FREQ + k] = fft_out_[pidx];
                imag_ptr[t * TSE_FREQ + k] = fft_out_[pidx + 1];
            }
            real_ptr[t * TSE_FREQ + TSE_FREQ - 1] = fft_out_[1];
            imag_ptr[t * TSE_FREQ + TSE_FREQ - 1] = 0.0f;
        }
    }

    void set_reference_py(const std::vector<float>& ref_audio) {
        set_reference(ref_audio);
    }

    // ── Debug dump helpers ──
    static void dump_bin(const std::string& path, const float* data, size_t n) {
        FILE* f = fopen(path.c_str(), "wb");
        if (f) { fwrite(data, sizeof(float), n, f); fclose(f); }
    }
    static void stat_line(const char* tag, const float* data, size_t n, int frame) {
        float lo = data[0], hi = data[0]; double sum = 0, sq = 0;
        for (size_t i = 0; i < n; ++i) {
            float v = data[i];
            if (v < lo) lo = v; if (v > hi) hi = v;
            sum += v; sq += (double)v * v;
        }
        float rms = (n > 0) ? (float)std::sqrt(sq / n) : 0;
        printf("[TSE15 dbg] f%02d %-12s | min=%+.4f max=%+.4f rms=%.4f\n", frame, tag, lo, hi, rms);
    }

    /// Process one 1024-sample chunk through TSE15 ONNX → 1024 output samples
    /// Streaming STFT: uses zero-initialized history for the first frame (no look-ahead).
    /// Python uses center=True reflect padding for the first frame — this causes minor
    /// differences in the first ~2 frames; output converges after warm-up (NFFT/HOP frames).
    void process_chunk(const float* input_1024, float* output_1024) {
        if (enr_buf_.empty()) {
            std::memcpy(output_1024, input_1024, TSE_HOP * sizeof(float));
            return;
        }

        // ── 1. Build FFT frame ──
        std::memcpy(fft_in_, input_history_.data(),
                    (TSE_NFFT - TSE_HOP) * sizeof(float));
        std::memcpy(fft_in_ + TSE_NFFT - TSE_HOP, input_1024,
                    TSE_HOP * sizeof(float));

        // Update history
        std::memmove(input_history_.data(), input_history_.data() + TSE_HOP,
                     (TSE_NFFT - 2 * TSE_HOP) * sizeof(float));
        std::memcpy(input_history_.data() + TSE_NFFT - 2 * TSE_HOP,
                    input_1024, TSE_HOP * sizeof(float));

        // ── 2. Window + FFT ──
        for (int i = 0; i < TSE_NFFT; ++i) fft_in_[i] *= window_[i];
        pffft_transform_ordered(fft_plan_, fft_in_, fft_out_, nullptr, PFFFT_FORWARD);

        // ── 3. PFFFT → planar complex spec (1,2,1,1025) ──
        float* real_part = spec_buf_.data();
        float* imag_part = spec_buf_.data() + TSE_FREQ;
        real_part[0] = fft_out_[0]; imag_part[0] = 0.0f;
        for (int k = 1; k < TSE_FREQ - 1; ++k) {
            int pidx = 2 + (k - 1) * 2;
            real_part[k] = fft_out_[pidx];
            imag_part[k] = fft_out_[pidx + 1];
        }
        real_part[TSE_FREQ - 1] = fft_out_[1]; imag_part[TSE_FREQ - 1] = 0.0f;

        int fc = frame_count_++;

        if (debug_dump_) {
            char path[512];
            snprintf(path, sizeof(path), "%s/f%02d_in.bin", debug_dir_.c_str(), fc);
            dump_bin(path, input_1024, TSE_HOP);
            snprintf(path, sizeof(path), "%s/f%02d_fft.bin", debug_dir_.c_str(), fc);
            dump_bin(path, fft_in_, TSE_NFFT);  // windowed FFT input
            snprintf(path, sizeof(path), "%s/f%02d_spec.bin", debug_dir_.c_str(), fc);
            dump_bin(path, spec_buf_.data(), TSE_SPEC_FLOATS);
        }

        // ── 4. ONNX inference (set TSE_BYPASS_ONNX=1 to test FFT/OLA passthrough) ──
#define TSE_BYPASS_ONNX 0
#if TSE_BYPASS_ONNX
        // Passthrough test: skip ONNX, keep original STFT, verify IFFT/OLA
        if (fc == 0) printf("[TSE15] BYPASS ONNX — testing FFT/OLA passthrough\n");
#else
        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);
        std::vector<int64_t> spec_shape  = {1, 2, 1, TSE_FREQ};
        std::vector<int64_t> enr_shape   = {1, TSE_ENR_CH,
                                            static_cast<int64_t>(enr_spec_ref_frames_),
                                            TSE_FREQ};
        std::vector<int64_t> cache_shape = {static_cast<int64_t>(CACHE_TOTAL)};

        std::vector<Ort::Value> inputs;
        inputs.reserve(3);
        for (const auto& name : input_names_) {
            if (name == "spec_frame")
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, spec_buf_.data(), TSE_SPEC_FLOATS,
                    spec_shape.data(), spec_shape.size()));
            else if (name == "enr_spec")
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, enr_buf_.data(), enr_buf_.size(),
                    enr_shape.data(), enr_shape.size()));
            else if (name == "cache_in")
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, cache_.data(), CACHE_TOTAL,
                    cache_shape.data(), cache_shape.size()));
        }

        std::vector<const char*> in_names(input_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i)
            in_names[i] = input_names_[i].c_str();
        std::vector<const char*> out_names(output_names_.size());
        for (size_t i = 0; i < output_names_.size(); ++i)
            out_names[i] = output_names_[i].c_str();

        auto outputs = session_->Run(Ort::RunOptions{nullptr},
                                     in_names.data(), inputs.data(), inputs.size(),
                                     out_names.data(), out_names.size());

        // ── 5. Read enhanced_spec + updated cache ──
        //    enh_frame is already mask * mix_spec: (1,2,1,1025) = 2050 floats
        //    cache_out is the updated flat cache: (319040,) = 319040 floats
        for (size_t i = 0; i < output_names_.size(); ++i) {
            const auto& name = output_names_[i];
            float* data = outputs[i].GetTensorMutableData<float>();
            auto shape = outputs[i].GetTensorTypeAndShapeInfo().GetShape();
            size_t total = 1;
            for (auto d : shape) { if (d <= 0) { total = 0; break; } total *= static_cast<size_t>(d); }

            if (name == "enh_frame") {
                std::memcpy(spec_buf_.data(), data,
                            std::min(total, spec_buf_.size()) * sizeof(float));
            } else if (name == "cache_out") {
                std::memcpy(cache_.data(), data,
                            std::min(total, CACHE_TOTAL) * sizeof(float));
            }
        }

        if (debug_dump_) {
            stat_line("spec_raw", spec_buf_.data(), TSE_SPEC_FLOATS, fc);
        }
#endif // TSE_BYPASS_ONNX

        // ── 6. Planar complex → PFFFT packed → IFFT ──
        fft_out_[0] = spec_buf_[0];                    // DC real
        fft_out_[1] = spec_buf_[TSE_FREQ - 1];       // Nyquist real
        for (int k = 1; k < TSE_FREQ - 1; ++k) {
            int pidx = 2 + (k - 1) * 2;
            fft_out_[pidx]     = spec_buf_[k];
            fft_out_[pidx + 1] = spec_buf_[TSE_FREQ + k];
        }

        pffft_transform_ordered(fft_plan_, fft_out_, ifft_out_, nullptr, PFFFT_BACKWARD);

        // ── 7. Synthesis window + OLA ──
        float scale = 1.0f / TSE_NFFT;
        for (int i = 0; i < TSE_NFFT; ++i) {
            ifft_out_[i] *= scale * window_[i];
        }
        for (int i = 0; i < TSE_NFFT; ++i) {
            ola_accumulator_[i] += ifft_out_[i];
        }
        for (int i = 0; i < TSE_NFFT; ++i) {
            window_sum_[i] += window_[i] * window_[i];
        }

        // OLA priming: first frame only fills accumulator (matching TseProcessor reference)
        // Outputting before 2-frame overlap steady-state causes amplitude explosion at window edges
        if (!primed_) {
            primed_ = true;
            std::memcpy(output_1024, input_1024, TSE_HOP * sizeof(float));
        } else {
            for (int i = 0; i < TSE_HOP; ++i) {
                float norm = window_sum_[i];
                output_1024[i] = (norm > 1e-6f) ? (ola_accumulator_[i] / norm) : ola_accumulator_[i];
            }
        }

        if (debug_dump_) {
            stat_line("output", output_1024, TSE_HOP, fc);
            char path[512];
            snprintf(path, sizeof(path), "%s/f%02d_out.bin", debug_dir_.c_str(), fc);
            dump_bin(path, output_1024, TSE_HOP);
        }

        // Shift OLA buffers
        for (int i = 0; i < TSE_NFFT - TSE_HOP; ++i) {
            ola_accumulator_[i] = ola_accumulator_[i + TSE_HOP];
            window_sum_[i] = window_sum_[i + TSE_HOP];
        }
        for (int i = TSE_NFFT - TSE_HOP; i < TSE_NFFT; ++i) {
            ola_accumulator_[i] = 0.0f;
            window_sum_[i] = 0.0f;
        }
    }

    /// Pure frequency-domain ONNX: input planar spectrum → ONNX → output planar spectrum.
    /// No IFFT/OLA. Used in StftProcessor-based chaining.
    void process_spec_freq(const float* spec_in, float* spec_out) {
        std::memcpy(spec_buf_.data(), spec_in, TSE_SPEC_FLOATS * sizeof(float));
        frame_count_++;

        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);
        std::vector<int64_t> ss = {1, 2, 1, TSE_FREQ};
        std::vector<int64_t> es = {1, TSE_ENR_CH, (int64_t)enr_spec_ref_frames_, TSE_FREQ};
        std::vector<int64_t> cs = {(int64_t)CACHE_TOTAL};
        std::vector<Ort::Value> inputs; inputs.reserve(3);
        for (const auto& name : input_names_) {
            if (name == "spec_frame") inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, spec_buf_.data(), TSE_SPEC_FLOATS, ss.data(), ss.size()));
            else if (name == "enr_spec") inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, enr_buf_.data(), enr_buf_.size(), es.data(), es.size()));
            else if (name == "cache_in") inputs.push_back(Ort::Value::CreateTensor<float>(mem_info, cache_.data(), CACHE_TOTAL, cs.data(), cs.size()));
        }
        std::vector<const char*> in_names(input_names_.size()), out_names(output_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i) in_names[i] = input_names_[i].c_str();
        for (size_t i = 0; i < output_names_.size(); ++i) out_names[i] = output_names_[i].c_str();
        auto outputs = session_->Run(Ort::RunOptions{nullptr}, in_names.data(), inputs.data(), inputs.size(), out_names.data(), out_names.size());
        for (size_t i = 0; i < output_names_.size(); ++i) {
            const auto& name = output_names_[i];
            float* data = outputs[i].GetTensorMutableData<float>();
            auto sh = outputs[i].GetTensorTypeAndShapeInfo().GetShape();
            size_t total = 1; for (auto d : sh) { if (d <= 0) { total = 0; break; } total *= (size_t)d; }
            if (name == "enh_frame") std::memcpy(spec_buf_.data(), data, std::min(total, spec_buf_.size()) * sizeof(float));
            else if (name == "cache_out") std::memcpy(cache_.data(), data, std::min(total, CACHE_TOTAL) * sizeof(float));
        }
        std::memcpy(spec_out, spec_buf_.data(), TSE_SPEC_FLOATS * sizeof(float));
    }

    /// Process from pre-computed planar spectrum → time-domain output (convenience: ONNX + IFFT + OLA)
    void process_from_spec(const float* spec_planar, float* output_1024) {
        // ── Fill spec_buf_ with caller's spectrum ──
        std::memcpy(spec_buf_.data(), spec_planar, TSE_SPEC_FLOATS * sizeof(float));

        int fc = frame_count_++;

        // ── ONNX inference ──
        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);
        std::vector<int64_t> spec_shape  = {1, 2, 1, TSE_FREQ};
        std::vector<int64_t> enr_shape   = {1, TSE_ENR_CH,
                                            static_cast<int64_t>(enr_spec_ref_frames_),
                                            TSE_FREQ};
        std::vector<int64_t> cache_shape = {static_cast<int64_t>(CACHE_TOTAL)};

        std::vector<Ort::Value> inputs;
        inputs.reserve(3);
        for (const auto& name : input_names_) {
            if (name == "spec_frame")
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, spec_buf_.data(), TSE_SPEC_FLOATS,
                    spec_shape.data(), spec_shape.size()));
            else if (name == "enr_spec")
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, enr_buf_.data(), enr_buf_.size(),
                    enr_shape.data(), enr_shape.size()));
            else if (name == "cache_in")
                inputs.push_back(Ort::Value::CreateTensor<float>(
                    mem_info, cache_.data(), CACHE_TOTAL,
                    cache_shape.data(), cache_shape.size()));
        }
        std::vector<const char*> in_names(input_names_.size());
        for (size_t i = 0; i < input_names_.size(); ++i) in_names[i] = input_names_[i].c_str();
        std::vector<const char*> out_names(output_names_.size());
        for (size_t i = 0; i < output_names_.size(); ++i) out_names[i] = output_names_[i].c_str();

        auto outputs = session_->Run(Ort::RunOptions{nullptr},
                                     in_names.data(), inputs.data(), inputs.size(),
                                     out_names.data(), out_names.size());

        for (size_t i = 0; i < output_names_.size(); ++i) {
            const auto& name = output_names_[i];
            float* data = outputs[i].GetTensorMutableData<float>();
            auto shape = outputs[i].GetTensorTypeAndShapeInfo().GetShape();
            size_t total = 1;
            for (auto d : shape) { if (d <= 0) { total = 0; break; } total *= static_cast<size_t>(d); }
            if (name == "enh_frame")
                std::memcpy(spec_buf_.data(), data, std::min(total, spec_buf_.size()) * sizeof(float));
            else if (name == "cache_out")
                std::memcpy(cache_.data(), data, std::min(total, CACHE_TOTAL) * sizeof(float));
        }

        // ── IFFT + OLA (same as process_chunk tail) ──
        fft_out_[0] = spec_buf_[0];
        fft_out_[1] = spec_buf_[TSE_FREQ - 1];
        for (int k = 1; k < TSE_FREQ - 1; ++k) {
            int pidx = 2 + (k - 1) * 2;
            fft_out_[pidx]     = spec_buf_[k];
            fft_out_[pidx + 1] = spec_buf_[TSE_FREQ + k];
        }
        pffft_transform_ordered(fft_plan_, fft_out_, ifft_out_, nullptr, PFFFT_BACKWARD);

        float scale = 1.0f / TSE_NFFT;
        for (int i = 0; i < TSE_NFFT; ++i) ifft_out_[i] *= scale * window_[i];
        for (int i = 0; i < TSE_NFFT; ++i) ola_accumulator_[i] += ifft_out_[i];
        for (int i = 0; i < TSE_NFFT; ++i) window_sum_[i] += window_[i] * window_[i];

        if (!primed_) { primed_ = true; }
        else {
            for (int i = 0; i < TSE_HOP; ++i) {
                float norm = window_sum_[i];
                output_1024[i] = (norm > 1e-6f) ? (ola_accumulator_[i] / norm) : ola_accumulator_[i];
            }
        }

        for (int i = 0; i < TSE_NFFT - TSE_HOP; ++i) {
            ola_accumulator_[i] = ola_accumulator_[i + TSE_HOP];
            window_sum_[i] = window_sum_[i + TSE_HOP];
        }
        for (int i = TSE_NFFT - TSE_HOP; i < TSE_NFFT; ++i) {
            ola_accumulator_[i] = 0.0f;
            window_sum_[i] = 0.0f;
        }
    }

    /// pybind11 wrapper
    std::vector<float> process_chunk_py(const std::vector<float>& input) {
        std::vector<float> output(TSE_HOP);
        process_chunk(input.data(), output.data());
        return output;
    }

    bool has_reference() const { return !enr_buf_.empty(); }

    void reset() {
        std::fill(cache_.begin(), cache_.end(), 0.0f);
        std::fill(input_history_.begin(), input_history_.end(), 0.0f);
        std::fill(ola_accumulator_.begin(), ola_accumulator_.end(), 0.0f);
        std::fill(window_sum_.begin(), window_sum_.end(), 0.0f);
        primed_ = false;
        frame_count_ = 0;
    }

    void set_debug_dump(bool enable, const std::string& dir = "") {
        debug_dump_ = enable;
        if (enable && !dir.empty()) {
            debug_dir_ = dir;
            // dump enr_buf if already computed
            if (!enr_buf_.empty()) {
                std::string p = debug_dir_ + "/debug_enr_spec.bin";
                dump_bin(p, enr_buf_.data(), enr_buf_.size());
                printf("[TSE15 dbg] enr_spec dumped: %zu floats, ref_frames=%zu\n", enr_buf_.size(), enr_spec_ref_frames_);
            }
        }
    }

    // ── Async worker thread (模型推理太重，独立线程避免阻塞主音频线程) ──
    void start_worker() {
        if (worker_running_) return;
        worker_stop_ = false;
        worker_running_ = true;
        worker_ = std::thread(&TseProcessor::worker_loop, this);
    }

    void stop_worker() {
        if (!worker_running_) return;
        worker_stop_ = true;
        if (worker_.joinable()) worker_.join();
        worker_running_ = false;
        input_buf_.clear();
        output_buf_.clear();
    }

    /// 非阻塞：推入输入，读取输出。返回 true 表示成功读到输出
    bool process_async(const float* input, float* output) {
        if (!worker_running_) {
            process_chunk(input, output);
            return true;
        }
        if (!input_buf_.write(input, TSE_HOP)) return false;
        if (output_buf_.try_read(output, TSE_HOP)) return true;
        std::memcpy(output, input, TSE_HOP * sizeof(float));  // fallback: passthrough
        return false;
    }

private:
    void worker_loop() {
        float input[TSE_HOP], result[TSE_HOP];
        while (!worker_stop_) {
            if (input_buf_.read_wait(input, TSE_HOP, 100)) {
                process_chunk(input, result);
                while (!worker_stop_ && !output_buf_.write(result, TSE_HOP)) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
                }
            }
        }
    }

    // Worker thread
    std::thread worker_;
    std::atomic<bool> worker_running_{false};
    std::atomic<bool> worker_stop_{false};
    TseRingBuffer input_buf_;
    TseRingBuffer output_buf_;

    // ONNX
    std::shared_ptr<Ort::Env> env_;
    std::shared_ptr<Ort::Session> session_;
    std::vector<std::string> input_names_;
    std::vector<std::string> output_names_;

    // PFFFT
    PFFFT_Setup* fft_plan_ = nullptr;
    float* fft_in_ = nullptr;
    float* fft_out_ = nullptr;
    float* ifft_out_ = nullptr;
    std::vector<float> window_;

    // OLA state
    std::vector<float> input_history_;
    std::vector<float> ola_accumulator_;
    std::vector<float> window_sum_;
    bool primed_ = false;

    // ONNX state tensor — single flat cache
    static constexpr size_t CACHE_TOTAL = 319040;
    std::vector<float> cache_;

    // Reference embedding (pre-computed raw STFT, planar: [real_Te×1025, imag_Te×1025])
    std::vector<float> enr_buf_;
    size_t enr_spec_ref_frames_ = 0;

    // Per-frame buffers
    std::vector<float> spec_buf_;   // (1,2,1,1025) = 2050

    // Debug
    int frame_count_ = 0;
    bool debug_dump_ = false;
    std::string debug_dir_;
};


// ============================================================================
// Resampler (libsamplerate wrapper)
// ============================================================================
class Resampler {
public:
    Resampler(int converter_type = SRC_SINC_FASTEST) {
        int error;
        m_state = src_new(converter_type, 1, &error);
        if (!m_state) {
            std::string msg = "Resampler init failed: ";
            msg += src_strerror(error);
            throw std::runtime_error(msg);
        }
    }

    ~Resampler() {
        if (m_state) {
            src_delete(m_state);
            m_state = nullptr;
        }
    }

    Resampler(const Resampler&) = delete;
    Resampler& operator=(const Resampler&) = delete;

    Resampler(Resampler&& other) noexcept : m_state(other.m_state) {
        other.m_state = nullptr;
    }

    std::vector<float> process(const std::vector<float>& input, double src_ratio, bool end_of_input = false) {
        if (input.empty() && !end_of_input) {
            return {};
        }
        if (src_ratio <= 0.0) {
            throw std::runtime_error("Resampler: src_ratio must be positive");
        }

        m_current_ratio = src_ratio;
        const float* input_ptr = input.data();
        long remaining = static_cast<long>(input.size());

        std::vector<float> output;
        std::vector<float> tmp_buffer(4096);

        // Process input frames
        while (remaining > 0) {
            SRC_DATA data;
            data.data_in = input_ptr;
            data.input_frames = remaining;
            data.data_out = tmp_buffer.data();
            data.output_frames = static_cast<long>(tmp_buffer.size());
            data.src_ratio = src_ratio;
            data.end_of_input = 0;

            int error = src_process(m_state, &data);
            if (error) {
                std::string msg = "Resampler process failed: ";
                msg += src_strerror(error);
                throw std::runtime_error(msg);
            }

            output.insert(output.end(), tmp_buffer.begin(),
                          tmp_buffer.begin() + data.output_frames_gen);

            remaining -= data.input_frames_used;
            input_ptr += data.input_frames_used;
        }

        // Flush internal filter buffer if end_of_input
        if (end_of_input) {
            while (true) {
                SRC_DATA data;
                data.data_in = nullptr;
                data.input_frames = 0;
                data.data_out = tmp_buffer.data();
                data.output_frames = static_cast<long>(tmp_buffer.size());
                data.src_ratio = m_current_ratio;
                data.end_of_input = 1;

                int error = src_process(m_state, &data);
                if (error) {
                    std::string msg = "Resampler flush failed: ";
                    msg += src_strerror(error);
                    throw std::runtime_error(msg);
                }

                if (data.output_frames_gen == 0)
                    break;

                output.insert(output.end(), tmp_buffer.begin(),
                              tmp_buffer.begin() + data.output_frames_gen);
            }
        }

        return output;
    }

    std::vector<float> flush() {
        std::vector<float> output;
        std::vector<float> tmp_buffer(4096);

        while (true) {
            SRC_DATA data;
            data.data_in = nullptr;
            data.input_frames = 0;
            data.data_out = tmp_buffer.data();
            data.output_frames = static_cast<long>(tmp_buffer.size());
            data.src_ratio = m_current_ratio;
            data.end_of_input = 1;

            int error = src_process(m_state, &data);
            if (error) {
                std::string msg = "Resampler flush failed: ";
                msg += src_strerror(error);
                throw std::runtime_error(msg);
            }

            if (data.output_frames_gen == 0)
                break;

            output.insert(output.end(), tmp_buffer.begin(),
                          tmp_buffer.begin() + data.output_frames_gen);
        }

        return output;
    }

    void reset() {
        src_reset(m_state);
    }

private:
    SRC_STATE* m_state = nullptr;
    double m_current_ratio = 1.0;
};


class AudioProcessor {
public:
    // Denoise only when mode == MODE_DENOISE
    static constexpr int MODE_PASSTHROUGH = 0;
    static constexpr int MODE_DENOISE     = 1;
    static constexpr int MODE_AEC         = 2;
    static constexpr int MODE_TSE         = 3;

    AudioProcessor(float pre_gain_db,
                   const std::string& denoise_model_path,
                   const std::string& tse_model_path = "",
                   const std::string& aec_model_path = "")
        : pre_gain_(std::pow(10.0f, pre_gain_db / 20.0f)),
          mode_(MODE_DENOISE),
          vad_enabled_(false),
          agc_enabled_(false) {

        // Initialize EQ filters (all gains = 0, flat response)
        eq_filters_.resize(EQ_BANDS);
        for (int i = 0; i < EQ_BANDS; ++i) {
            eq_filters_[i] = design_peaking_eq(EQ_FREQS[i], 0.0f, EQ_Q, SAMPLE_RATE);
        }

        // Initialize TSE processor if model path provided
        if (!tse_model_path.empty()) {
            tse_processor_ = std::make_unique<TseProcessor>(tse_model_path);
        }

        // Initialize AEC processor if model path provided
        if (!aec_model_path.empty()) {
            aec_processor_ = std::make_unique<AecProcessor>(aec_model_path);
        }

        // Initialize denoise processor if model path provided
        if (!denoise_model_path.empty()) {
            denoise_processor_ = std::make_unique<DenoiseProcessor>(denoise_model_path);
        }
    }

    ~AudioProcessor() {
        cleanup();
    }


    // Primary process: mic input only (no AEC).
    std::vector<float> process(const std::vector<float>& audio_chunk) {
        return process(audio_chunk, nullptr);
    }

    // Process with optional far-end audio for AEC.
    //
    // -- Unified processing chain (four mutually exclusive modes) --
    // pre_gain -> EQ -> clip -> [switch: passthrough|denoise|aec|tse] -> compressor -> clip -> VAD -> AGC
    //
    // AEC mic goes through pre_gain/EQ first. Far-end is raw.
    std::vector<float> process(const std::vector<float>& audio_chunk,
                               const std::vector<float>* far_end) {
        if (audio_chunk.size() != HOP_LENGTH)
            throw std::runtime_error("Input audio chunk length must be equal to hop length (1024)");

        std::vector<float> buf(HOP_LENGTH);
        std::memcpy(buf.data(), audio_chunk.data(), HOP_LENGTH * sizeof(float));

        // ── 共用前处理 ──
        apply_pre_gain(buf);
        apply_eq_clip(buf);

        // ── 模式分支（完全互斥）──
        std::vector<float> out;
        switch (mode_) {
        case MODE_PASSTHROUGH:
            out = std::move(buf);
            break;
        case MODE_DENOISE: {
            if (!denoise_processor_) { out = std::move(buf); break; }
            std::vector<float> out_vec(HOP_LENGTH);
            denoise_processor_->process_chunk(buf.data(), out_vec.data());
            out = std::move(out_vec);
            break;
        }
        case MODE_AEC:
            if (denoise_processor_) {
                std::vector<float> denoised(HOP_LENGTH);
                denoise_processor_->process_chunk(buf.data(), denoised.data());
                buf = std::move(denoised);
            }
            out = aec_step(buf, far_end);
            break;
        case MODE_TSE:
            out = tse_step(buf);
            break;
        default:
            out = std::move(buf);
            break;
        }

        // ── 共用后处理 ──
        if (compressor_enabled_) compressor_.process(out.data(), out.size());
        if (recording_enabled_ && mode_ != MODE_TSE) tse_recording_buffer_ = out;
        else if (!recording_enabled_) tse_recording_buffer_.clear();
        clip_buffer(out.data(), out.size());
        if (vad_enabled_) vad_gate_.process(out.data(), out.size());
        if (agc_enabled_) measure_agc_rms(out);
        return out;
    }

    // ── 模式分支实现 ──
    std::vector<float> aec_step(const std::vector<float>& buf, const std::vector<float>* far_end) {
        if (!aec_processor_ || !far_end || far_end->empty()) return buf;
        float aec_out[HOP_LENGTH];
        bool ok = false;
        if (far_resampler_ && far_sample_rate_ != 48000) {
            double ratio = 48000.0 / static_cast<double>(far_sample_rate_);
            auto resampled = far_resampler_->process(*far_end, ratio);
            if (resampled.size() >= HOP_LENGTH) {
                aec_processor_->process_frame(buf.data(), resampled.data(), aec_out);
                ok = true;
            }
        } else if (far_end->size() >= HOP_LENGTH) {
            aec_processor_->process_frame(buf.data(), far_end->data(), aec_out);
            ok = true;
        }
        return ok ? std::vector<float>(aec_out, aec_out + HOP_LENGTH) : buf;
    }

    std::vector<float> tse_step(const std::vector<float>& buf) {
        if (!tse_processor_ || !tse_processor_->has_reference()) return buf;
        tse_recording_buffer_ = buf;
        float tse_out[HOP_LENGTH];

        // 时域降噪 → STFT → TSE(频域) → ISTFT
        float spec[2050];
        if (denoise_processor_) {
            float denoised[HOP_LENGTH];
            denoise_processor_->process_chunk(buf.data(), denoised);
            stft_.forward(denoised, spec);
        } else {
            stft_.forward(buf.data(), spec);
        }

        tse_processor_->process_spec_freq(spec, spec);
        stft_.backward(spec, tse_out);

        return std::vector<float>(tse_out, tse_out + HOP_LENGTH);
    }

    // ── Mode (unified, four mutually exclusive modes) ──
    void set_mode(int mode) {
        mode_ = mode;
        stft_.reset();
        if (mode == MODE_TSE && tse_processor_) {
            tse_processor_->reset();
        }
    }
    int get_mode() const { return mode_; }

    void set_tse_enabled(bool en) { if (en) set_mode(MODE_TSE); else if (mode_ == MODE_TSE) set_mode(MODE_PASSTHROUGH); }
    void set_aec_enabled(bool en) { if (en) set_mode(MODE_AEC); else if (mode_ == MODE_AEC) set_mode(MODE_PASSTHROUGH); }
    void set_aec_far_sample_rate(int sr) {
        if (sr <= 0) sr = 48000;
        far_sample_rate_ = sr;
        if (sr != 48000 && aec_processor_) {
            far_resampler_ = std::make_unique<Resampler>();
            double ratio = 48000.0 / static_cast<double>(sr);
            std::vector<float> silence(HOP_LENGTH, 0.0f);
            far_resampler_->process(silence, ratio);
        } else { far_resampler_.reset(); }
    }
    int  get_aec_far_sample_rate() const { return far_sample_rate_; }
    void set_aec_far_rms_target(float rms) { far_rms_target_ = (rms > 0.0f) ? rms : 0.05f; }
    float get_aec_far_rms_target() const { return far_rms_target_; }
    bool is_aec_available() const { return aec_processor_ != nullptr; }

    void set_tse_reference(const std::vector<float>& ref_audio) { if (tse_processor_) tse_processor_->set_reference(ref_audio); }
    bool is_tse_reference_loaded() const { return tse_processor_ && tse_processor_->has_reference(); }
    bool is_tse_available() const { return tse_processor_ != nullptr; }
    const std::vector<float>& get_tse_recording_audio() const { return tse_recording_buffer_; }

    // ── VAD / AGC / Recording ──
    void set_vad_enabled(bool enabled) {
        if (enabled && !vad_enabled_) vad_gate_.reset();
        vad_enabled_ = enabled;
    }
    bool is_vad_enabled() const { return vad_enabled_; }
    bool is_vad_active() const { return vad_gate_.is_active(); }
    void set_vad_threshold(float dbfs) { vad_gate_.set_threshold(dbfs); }
    float get_vad_threshold() const { return vad_gate_.threshold_dbfs(); }

    // ── AGC control ──
    void set_agc_enabled(bool enabled, float initial_gain_db = 0.0f) {
        agc_.set_enabled(enabled, initial_gain_db);
        agc_enabled_ = enabled;
    }
    bool is_agc_enabled() const { return agc_enabled_; }
    float get_agc_gain_db() const { return agc_.get_current_gain_db(); }
    bool is_agc_voice_active() const { return agc_.is_voice_active(); }
    void set_agc_target(float dbfs) { agc_.set_target(dbfs); }
    float get_agc_target() const { return agc_.target_dbfs(); }

    // ── Compressor control ──
    void set_compressor_enabled(bool enabled) {
        compressor_enabled_ = enabled;
        compressor_.set_enabled(enabled);
    }
    bool is_compressor_enabled() const { return compressor_enabled_; }
    void set_compressor_threshold(float db) { compressor_.set_threshold(db); }
    float get_compressor_threshold() const { return compressor_.get_threshold(); }
    void set_compressor_ratio(float r) { compressor_.set_ratio(r); }
    float get_compressor_ratio() const { return compressor_.get_ratio(); }
    void set_compressor_attack(float ms) { compressor_.set_attack_ms(ms); }
    float get_compressor_attack() const { return compressor_.get_attack_ms(); }
    void set_compressor_release(float ms) { compressor_.set_release_ms(ms); }
    float get_compressor_release() const { return compressor_.get_release_ms(); }
    void set_compressor_makeup(float db) { compressor_.set_makeup(db); }
    float get_compressor_makeup() const { return compressor_.get_makeup(); }
    void set_compressor_knee(float db) { compressor_.set_knee(db); }
    float get_compressor_knee() const { return compressor_.get_knee(); }

    // ── Recording mode (bypass AGC/VAD, use TSE gains, fill recording buffer) ──
    void set_recording_enabled(bool enabled) { recording_enabled_ = enabled; }
    bool is_recording_enabled() const { return recording_enabled_; }

    // ── 双缓冲 pipeline ──
    void set_io_sample_rates(int in_sr, int out_sr) {
        io_in_sr_ = in_sr; io_out_sr_ = out_sr;
        io_in_acc_.clear(); io_out_acc_.clear();
    }

    std::vector<float> process_pipeline(const std::vector<float>& raw_input,
                                        const std::vector<float>* far_end = nullptr) {
        std::vector<float> output;
        if (raw_input.empty()) return output;

        // 直接处理 48kHz 数据，无重采样
        io_in_acc_.insert(io_in_acc_.end(), raw_input.begin(), raw_input.end());
        while (io_in_acc_.size() >= HOP_LENGTH) {
            std::vector<float> chunk(io_in_acc_.begin(), io_in_acc_.begin() + HOP_LENGTH);
            io_in_acc_.erase(io_in_acc_.begin(), io_in_acc_.begin() + HOP_LENGTH);
            viz_in_48k_.insert(viz_in_48k_.end(), chunk.begin(), chunk.end());
            auto p = process(chunk, far_end);
            viz_out_48k_.insert(viz_out_48k_.end(), p.begin(), p.end());
            io_out_acc_.insert(io_out_acc_.end(), p.begin(), p.end());
        }
        if (io_in_acc_.size() >= HOP_LENGTH * 3 / 4) {
            size_t orig_sz = io_in_acc_.size();
            io_in_acc_.resize(HOP_LENGTH, 0.0f);
            viz_in_48k_.insert(viz_in_48k_.end(), io_in_acc_.begin(), io_in_acc_.begin() + orig_sz);
            auto p = process(io_in_acc_, far_end);
            viz_out_48k_.insert(viz_out_48k_.end(), p.begin(), p.end());
            io_out_acc_.insert(io_out_acc_.end(), p.begin(), p.end());
            io_in_acc_.clear();
        }
        output.swap(io_out_acc_);
        return output;
    }

    /// 获取并清空 48kHz 可视化缓冲区（Python 端直接使用，无需二次重采样）
    std::vector<float> get_and_clear_viz_input() {
        std::vector<float> r; r.swap(viz_in_48k_); return r;
    }
    std::vector<float> get_and_clear_viz_output() {
        std::vector<float> r; r.swap(viz_out_48k_); return r;
    }

    void set_eq_gains(const std::vector<float>& gains) {
        bool any_nonzero = false;
        for (int i = 0; i < EQ_BANDS; ++i) {
            float g = (i < (int)gains.size()) ? gains[i] : 0.0f;
            if (g != 0.0f) any_nonzero = true;
            eq_filters_[i] = design_peaking_eq(EQ_FREQS[i], g, EQ_Q, SAMPLE_RATE);
        }
        eq_active_ = any_nonzero;
    }

    std::vector<float> get_eq_freqs() const {
        return std::vector<float>(EQ_FREQS, EQ_FREQS + EQ_BANDS);
    }

    int get_eq_band_count() const {
        return EQ_BANDS;
    }

    // Process audio through pre_gain + EQ only (for spectrum display)
    std::vector<float> process_eq_only(const std::vector<float>& audio_chunk) {
        size_t len = audio_chunk.size();
        std::vector<float> buf(len);
        std::memcpy(buf.data(), audio_chunk.data(), len * sizeof(float));
        // Pre-gain
        float g = agc_enabled_ ? agc_.tick() : pre_gain_;
        for (size_t i = 0; i < len; ++i) {
            buf[i] *= g;
        }
        // EQ
        if (eq_active_) {
            apply_eq(buf.data(), buf.size());
        }
        return buf;
    }

    void set_pre_gain(float gain_db) {
        pre_gain_ = std::pow(10.0f, gain_db / 20.0f);
    }

    void cleanup() {
        denoise_processor_.reset();
        tse_processor_.reset();
        aec_processor_.reset();
    }

private:
    float pre_gain_;
    std::vector<BiquadCoeff> eq_filters_;
    int mode_ = MODE_DENOISE;  // 0=passthrough 1=denoise 2=aec 3=tse
    bool eq_active_ = false;

    // TSE / AEC / Denoise processors (created at construction, used by mode)
    std::unique_ptr<DenoiseProcessor> denoise_processor_;
    std::unique_ptr<TseProcessor>   tse_processor_;
    std::unique_ptr<AecProcessor>     aec_processor_;
    StftProcessor                     stft_;                // unified STFT engine
    int far_sample_rate_ = 48000;
    std::unique_ptr<Resampler> far_resampler_;
    float far_rms_target_ = 0.05f;

    // pipeline I/O
    int io_in_sr_ = 48000;
    int io_out_sr_ = 48000;
    std::vector<float> io_in_acc_;
    std::vector<float> io_out_acc_;

    // 48kHz 可视化缓冲区（直接从 pipeline 内部获取，避免 Python 侧二次重采样）
    std::vector<float> viz_in_48k_;   // 重采样到 48k 后的输入（pre_gain+EQ+clip 前）
    std::vector<float> viz_out_48k_;  // process() 输出的 48k 数据（重采样到设备采样率前）

    // VAD (Voice Activity Detection)
    VadGate vad_gate_;
    bool vad_enabled_ = false;

    // AGC (Automatic Gain Control)
    AgcController agc_;
    bool agc_enabled_ = false;

    // Compressor
    Compressor compressor_;
    bool compressor_enabled_ = false;

    std::vector<float> tse_recording_buffer_;
    bool recording_enabled_ = false;

    void apply_eq(float* data, size_t len) {
        for (size_t n = 0; n < len; ++n) {
            float y = data[n];
            for (auto& filter : eq_filters_) {
                float x = y;
                y = filter.b0 * x + filter.b1 * filter.x1 + filter.b2 * filter.x2
                     - filter.a1 * filter.y1 - filter.a2 * filter.y2;
                filter.x2 = filter.x1;
                filter.x1 = x;
                filter.y2 = filter.y1;
                filter.y1 = y;
            }
            data[n] = y;
        }
    }

    // -- Processing chain sub-steps --

    void apply_pre_gain(std::vector<float>& buf) {
        float g = agc_enabled_ ? agc_.tick() : pre_gain_;
        for (size_t i = 0; i < HOP_LENGTH; ++i) {
            buf[i] *= g;
        }
    }

    void apply_eq_clip(std::vector<float>& buf) {
        if (eq_active_) {
            apply_eq(buf.data(), buf.size());
        }
        clip_buffer(buf.data(), buf.size());
    }

    void measure_agc_rms(const std::vector<float>& out) {
        float sq = 0.0f;
        for (size_t i = 0; i < HOP_LENGTH; ++i) {
            sq += out[i] * out[i];
        }
        float rms = std::sqrt(sq / static_cast<float>(HOP_LENGTH));
        agc_.update_rms(rms);
    }
};

// ============================================================================
// Spectrum computation
// Spectrum computation (128-band Mel, matching human perception)
// ============================================================================

static const int SPECTRUM_FFT_SIZE = 2048;
static const int SPECTRUM_NUM_BANDS = 128;
static const float SPECTRUM_SAMPLE_RATE = 48000.0f;
static const float MEL_LOW_FREQ = 20.0f;
static const float MEL_HIGH_FREQ = 20000.0f;

// Mel filterbank: [num_bins][num_bands] sparse weights
static float* mel_filterbank_weights = nullptr;
static int* mel_filterbank_starts = nullptr;
static int* mel_filterbank_ends = nullptr;
static int mel_num_bins = 0;
static bool mel_initialized = false;

static inline float hz_to_mel(float hz) {
    return 2595.0f * std::log10(1.0f + hz / 700.0f);
}

static inline float mel_to_hz(float mel) {
    return 700.0f * (std::pow(10.0f, mel / 2595.0f) - 1.0f);
}

static void init_mel_filterbank() {
    if (mel_initialized) return;

    int n_fft = SPECTRUM_FFT_SIZE;
    mel_num_bins = n_fft / 2 + 1;
    float f_max = SPECTRUM_SAMPLE_RATE / 2.0f;
    float mel_max = hz_to_mel(MEL_HIGH_FREQ);
    float mel_min = hz_to_mel(MEL_LOW_FREQ);

    std::vector<float> center_freqs(SPECTRUM_NUM_BANDS + 2);
    for (int i = 0; i < SPECTRUM_NUM_BANDS + 2; ++i) {
        float mel = mel_min + (mel_max - mel_min) * i / (SPECTRUM_NUM_BANDS + 1);
        center_freqs[i] = mel_to_hz(mel);
    }

    // For each FFT bin, find which bands it contributes to and the weight
    // Store as sparse: for each bin, list of (band, weight)
    std::vector<std::vector<std::pair<int, float>>> bin_to_bands(mel_num_bins);

    for (int b = 0; b < SPECTRUM_NUM_BANDS; ++b) {
        float f_left = center_freqs[b];
        float f_center = center_freqs[b + 1];
        float f_right = center_freqs[b + 2];

        for (int k = 0; k < mel_num_bins; ++k) {
            float freq = (float)k * SPECTRUM_SAMPLE_RATE / n_fft;
            float weight = 0.0f;
            if (freq >= f_left && freq <= f_center && f_center > f_left) {
                weight = (freq - f_left) / (f_center - f_left);
            } else if (freq > f_center && freq <= f_right && f_right > f_center) {
                weight = (f_right - freq) / (f_right - f_center);
            }
            if (weight > 0.0f) {
                bin_to_bands[k].push_back({b, weight});
            }
        }
    }

    // Flatten into arrays for fast access
    int total_entries = 0;
    for (int k = 0; k < mel_num_bins; ++k) {
        total_entries += (int)bin_to_bands[k].size();
    }

    mel_filterbank_weights = new float[total_entries];
    mel_filterbank_starts = new int[mel_num_bins + 1];
    mel_filterbank_ends = new int[total_entries];

    int pos = 0;
    for (int k = 0; k < mel_num_bins; ++k) {
        mel_filterbank_starts[k] = pos;
        for (auto& [band, weight] : bin_to_bands[k]) {
            mel_filterbank_weights[pos] = weight;
            mel_filterbank_ends[pos] = band;
            ++pos;
        }
    }
    mel_filterbank_starts[mel_num_bins] = pos;

    mel_initialized = true;
}

std::vector<float> compute_spectrum(const std::vector<float>& samples) {
    init_mel_filterbank();

    static PFFFT_Setup* spectrum_setup = nullptr;
    static float* spectrum_fft_in = nullptr;
    static float* spectrum_fft_out = nullptr;
    static float* spectrum_buf = nullptr;
    static const int n = SPECTRUM_FFT_SIZE;

    if (!spectrum_setup) {
        spectrum_setup = pffft_new_setup(n, PFFFT_REAL);
        if (!spectrum_setup) return std::vector<float>(SPECTRUM_NUM_BANDS, -90.0f);
        spectrum_fft_in = (float*)pffft_aligned_malloc(n * sizeof(float));
        spectrum_fft_out = (float*)pffft_aligned_malloc(n * sizeof(float));
        spectrum_buf = (float*)pffft_aligned_malloc(n * sizeof(float));
        if (!spectrum_fft_in || !spectrum_fft_out || !spectrum_buf) {
            // If any allocation failed, the function returns -90 dB for all bands
            pffft_aligned_free(spectrum_fft_in);
            pffft_aligned_free(spectrum_fft_out);
            pffft_aligned_free(spectrum_buf);
            spectrum_fft_in = spectrum_fft_out = spectrum_buf = nullptr;
            return std::vector<float>(SPECTRUM_NUM_BANDS, -90.0f);
        }
    }

    // Copy samples (last N samples, zero-padded if shorter)
    int copy_len = std::min((int)samples.size(), n);
    std::fill(spectrum_buf, spectrum_buf + n, 0.0f);
    if (copy_len > 0) {
        int start = (int)samples.size() - copy_len;
        std::copy(samples.begin() + start, samples.begin() + start + copy_len, spectrum_buf);
    }

    // Hann window
    for (int i = 0; i < n; ++i) {
        float w = 0.5f - 0.5f * std::cos(2.0f * (float)M_PI * i / n);
        spectrum_buf[i] *= w;
    }

    // Forward real FFT (RFFT) via PFFFT — ordered output
    std::copy(spectrum_buf, spectrum_buf + n, spectrum_fft_in);
    pffft_transform_ordered(spectrum_setup, spectrum_fft_in, spectrum_fft_out, nullptr, PFFFT_FORWARD);

    // Power spectrum from PFFFT ordered real output:
    //   fft_out[0] = DC (real), fft_out[1] = Nyquist (real)
    //   fft_out[2k] = re(k), fft_out[2k+1] = im(k),  k = 1..N/2-1
    int num_bins = n / 2 + 1;
    std::vector<float> power(num_bins);
    float scale = 1.0f / (float)(n * n);
    power[0] = spectrum_fft_out[0] * spectrum_fft_out[0] * scale;
    for (int k = 1; k < n / 2; ++k) {
        float re = spectrum_fft_out[2 * k];
        float im = spectrum_fft_out[2 * k + 1];
        power[k] = (re * re + im * im) * scale;
    }
    power[n / 2] = spectrum_fft_out[1] * spectrum_fft_out[1] * scale;

    // Apply Mel filterbank (sparse matrix multiply)
    std::vector<float> mel_energy(SPECTRUM_NUM_BANDS, 0.0f);
    for (int k = 0; k < mel_num_bins; ++k) {
        int start = mel_filterbank_starts[k];
        int end = mel_filterbank_starts[k + 1];
        for (int j = start; j < end; ++j) {
            int band = mel_filterbank_ends[j];
            float weight = mel_filterbank_weights[j];
            mel_energy[band] += power[k] * weight;
        }
    }

    // Convert to dB
    std::vector<float> result(SPECTRUM_NUM_BANDS, -90.0f);
    for (int i = 0; i < SPECTRUM_NUM_BANDS; ++i) {
        if (mel_energy[i] > 1e-12f) {
            float db = 10.0f * std::log10(mel_energy[i]);
            result[i] = std::max(-90.0f, std::min(-20.0f, db));
        }
    }
    return result;
}

void spectrum_warmup() {
    init_mel_filterbank();

    static const int n = SPECTRUM_FFT_SIZE;
    static PFFFT_Setup* warmup_setup = nullptr;
    static float* warmup_in = nullptr;
    static float* warmup_out = nullptr;
    static float* warmup_buf = nullptr;

    if (!warmup_setup) {
        warmup_setup = pffft_new_setup(n, PFFFT_REAL);
        if (!warmup_setup) return;
        warmup_in = (float*)pffft_aligned_malloc(n * sizeof(float));
        warmup_out = (float*)pffft_aligned_malloc(n * sizeof(float));
        warmup_buf = (float*)pffft_aligned_malloc(n * sizeof(float));
        if (!warmup_in || !warmup_out || !warmup_buf) {
            pffft_aligned_free(warmup_in);
            pffft_aligned_free(warmup_out);
            pffft_aligned_free(warmup_buf);
            warmup_in = warmup_out = warmup_buf = nullptr;
        }
    }
}

// ── C++ RingBuffer（线程安全FIFO，供Python端高频读写）──
class RingBuffer {
public:
    RingBuffer(size_t capacity)
        : buffer_(capacity, 0.0f), capacity_(capacity), write_pos_(0), read_pos_(0), count_(0) {}

    void write(const float* data, size_t n) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (n > capacity_) n = capacity_;
        for (size_t i = 0; i < n; ++i) {
            buffer_[write_pos_++] = data[i];
            if (write_pos_ >= capacity_) write_pos_ = 0;
            if (count_ < capacity_) ++count_;
            else { read_pos_ = write_pos_; }
        }
    }

    size_t read(float* dest, size_t n) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (n > count_) n = count_;
        for (size_t i = 0; i < n; ++i) {
            dest[i] = buffer_[read_pos_++];
            if (read_pos_ >= capacity_) read_pos_ = 0;
            --count_;
        }
        return n;
    }

    size_t available() const { std::lock_guard<std::mutex> lock(mutex_); return count_; }
    void clear() { std::lock_guard<std::mutex> lock(mutex_); write_pos_ = read_pos_ = count_ = 0; std::fill(buffer_.begin(), buffer_.end(), 0.0f); }

private:
    mutable std::mutex mutex_;
    std::vector<float> buffer_;
    size_t capacity_;
    size_t write_pos_;
    size_t read_pos_;
    size_t count_;
};

PYBIND11_MODULE(aimic, m) {
    #ifdef _WIN32
    HMODULE hModule = NULL;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)&PyInit_aimic, &hModule);
    if (hModule != NULL) {
        char module_path[MAX_PATH];
        GetModuleFileNameA(hModule, module_path, MAX_PATH);
        char* last_slash = strrchr(module_path, '\\');
        if (last_slash != NULL) {
            *last_slash = '\0';
            
            char onnxruntime_path[MAX_PATH];
            snprintf(onnxruntime_path, MAX_PATH, "%s\\onnxruntime.dll", module_path);
            
            if (LoadLibraryA(onnxruntime_path) == NULL) {
                snprintf(onnxruntime_path, MAX_PATH, "%s\\cpp\\onnxruntime-win-x64-1.24.4\\lib\\onnxruntime.dll", module_path);
                if (LoadLibraryA(onnxruntime_path) == NULL) {
                    LoadLibraryA("onnxruntime.dll");
                }
            }
        }
    }
    #endif


    py::class_<AudioProcessor>(m, "AudioProcessor")
        .def(py::init<float, const std::string&, const std::string&, const std::string&>(),
             py::arg("pre_gain_db"),
             py::arg("denoise_model_path"),
             py::arg("tse_model_path") = "",
             py::arg("aec_model_path") = "")
        .def("cleanup", &AudioProcessor::cleanup)
        .def("set_eq_gains", &AudioProcessor::set_eq_gains)
        .def("get_eq_freqs", &AudioProcessor::get_eq_freqs)
        .def("get_eq_band_count", &AudioProcessor::get_eq_band_count)
        .def("process_eq_only", &AudioProcessor::process_eq_only)
        .def("set_pre_gain", &AudioProcessor::set_pre_gain)
        .def("set_mode", &AudioProcessor::set_mode)
        .def("get_mode", &AudioProcessor::get_mode)
        .def("set_tse_enabled", &AudioProcessor::set_tse_enabled)
        .def("get_tse_recording_audio", &AudioProcessor::get_tse_recording_audio)
        .def("set_tse_reference", &AudioProcessor::set_tse_reference)
        .def("is_tse_reference_loaded", &AudioProcessor::is_tse_reference_loaded)
        .def("is_tse_available", &AudioProcessor::is_tse_available)
        // VAD
        .def("set_vad_enabled", &AudioProcessor::set_vad_enabled)
        .def("is_vad_enabled", &AudioProcessor::is_vad_enabled)
        .def("is_vad_active", &AudioProcessor::is_vad_active)
        .def("set_vad_threshold", &AudioProcessor::set_vad_threshold)
        .def("get_vad_threshold", &AudioProcessor::get_vad_threshold)
        // AGC
        .def("set_agc_enabled", [](AudioProcessor& self, bool enabled, float init_db) {
            self.set_agc_enabled(enabled, init_db);
        }, py::arg("enabled"), py::arg("initial_gain_db") = 0.0f)
        .def("is_agc_enabled", &AudioProcessor::is_agc_enabled)
        .def("is_agc_voice_active", &AudioProcessor::is_agc_voice_active)
        .def("set_recording_enabled", &AudioProcessor::set_recording_enabled)
        .def("is_recording_enabled", &AudioProcessor::is_recording_enabled)
        .def("get_agc_gain_db", &AudioProcessor::get_agc_gain_db)
        .def("set_agc_target", &AudioProcessor::set_agc_target)
        .def("get_agc_target", &AudioProcessor::get_agc_target)
        // Compressor
        .def("set_compressor_enabled", &AudioProcessor::set_compressor_enabled)
        .def("is_compressor_enabled", &AudioProcessor::is_compressor_enabled)
        .def("set_compressor_threshold", &AudioProcessor::set_compressor_threshold)
        .def("get_compressor_threshold", &AudioProcessor::get_compressor_threshold)
        .def("set_compressor_ratio", &AudioProcessor::set_compressor_ratio)
        .def("get_compressor_ratio", &AudioProcessor::get_compressor_ratio)
        .def("set_compressor_attack", &AudioProcessor::set_compressor_attack)
        .def("get_compressor_attack", &AudioProcessor::get_compressor_attack)
        .def("set_compressor_release", &AudioProcessor::set_compressor_release)
        .def("get_compressor_release", &AudioProcessor::get_compressor_release)
        .def("set_compressor_makeup", &AudioProcessor::set_compressor_makeup)
        .def("get_compressor_makeup", &AudioProcessor::get_compressor_makeup)
        .def("set_compressor_knee", &AudioProcessor::set_compressor_knee)
        .def("get_compressor_knee", &AudioProcessor::get_compressor_knee)
        // AEC
        .def("set_aec_enabled", &AudioProcessor::set_aec_enabled)
        .def("is_aec_available", &AudioProcessor::is_aec_available)
        .def("set_aec_far_sample_rate", &AudioProcessor::set_aec_far_sample_rate)
        .def("get_aec_far_sample_rate", &AudioProcessor::get_aec_far_sample_rate)
        .def("set_aec_far_rms_target", &AudioProcessor::set_aec_far_rms_target)
        .def("get_aec_far_rms_target", &AudioProcessor::get_aec_far_rms_target)
        // process with far-end audio for AEC
        .def("process", py::overload_cast<const std::vector<float>&>(&AudioProcessor::process))
        .def("process_with_far", [](AudioProcessor& self, const std::vector<float>& mic,
                                     const std::vector<float>& far_end) {
            return self.process(mic, &far_end);
        })
        .def("set_io_sample_rates", &AudioProcessor::set_io_sample_rates)
        .def("process_pipeline", [](AudioProcessor& self, const std::vector<float>& raw_input,
                                     py::object far_end = py::none()) {
            if (far_end.is_none()) {
                return self.process_pipeline(raw_input);
            } else {
                auto fe = far_end.cast<std::vector<float>>();
                return self.process_pipeline(raw_input, &fe);
            }
        }, py::arg("raw_input"), py::arg("far_end") = py::none())
        .def("get_and_clear_viz_input", &AudioProcessor::get_and_clear_viz_input)
        .def("get_and_clear_viz_output", &AudioProcessor::get_and_clear_viz_output);


    py::class_<TseProcessor>(m, "TseProcessor")
        .def(py::init<const std::string&>(), py::arg("model_path"))
        .def("set_reference", &TseProcessor::set_reference_py)
        .def("has_reference", &TseProcessor::has_reference)
        .def("process_chunk", &TseProcessor::process_chunk_py)
        .def("reset", &TseProcessor::reset)
        .def("set_debug_dump", &TseProcessor::set_debug_dump);

    py::class_<AecProcessor>(m, "AecProcessor")
        .def(py::init<const std::string&>(), py::arg("model_path"))
        .def("process_frame", &AecProcessor::process_frame_py)
        .def("reset", &AecProcessor::reset);

    py::class_<Resampler>(m, "Resampler")
        .def(py::init<int>(), py::arg("converter_type") = static_cast<int>(SRC_SINC_FASTEST))
        .def("process", &Resampler::process,
             py::arg("input"), py::arg("src_ratio"), py::arg("end_of_input") = false)
        .def("reset", &Resampler::reset);

    m.attr("SRC_SINC_FASTEST") = py::int_(static_cast<int>(SRC_SINC_FASTEST));

    // Spectrum computation (61-band)
    m.def("compute_spectrum", &compute_spectrum,
          py::arg("samples"),
          "Compute 128-band Mel spectrum from audio samples (returns dB values, matches EQ bands)");
    m.def("spectrum_warmup", &spectrum_warmup,
          "Pre-initialize spectrum FFT and Mel filterbank (call once at startup)");
    m.attr("SPECTRUM_NUM_BANDS") = SPECTRUM_NUM_BANDS;

    // RingBuffer for Python
    py::class_<RingBuffer>(m, "RingBuffer")
        .def(py::init<size_t>(), py::arg("capacity"))
        .def("write", [](RingBuffer& self, const std::vector<float>& data) {
            self.write(data.data(), data.size());
        })
        .def("read", [](RingBuffer& self, size_t n) -> std::vector<float> {
            std::vector<float> out(n);
            size_t got = self.read(out.data(), n);
            out.resize(got);
            return out;
        })
        .def("available", &RingBuffer::available)
        .def("clear", &RingBuffer::clear);

    // Warm-up spectrum compute on module load (avoids lazy-init delay on first call)
    spectrum_warmup();
}