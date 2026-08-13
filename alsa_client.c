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
 * pvalsa — PureVox native ALSA bridge (pure C shared lib, Linux only,
 *          ctypes binding is pvalsa.py).
 *
 * Linux secondary audio backend (default remains native PipeWire via
 * libpvpipe.so).  Used when the user picks the "ALSA" local interface
 * in the UI, e.g. on PipeWire-less / minimal systems.
 *
 * Structure (AlsaBridge), symmetric to PwBridge:
 *   - in_pcm   (capture)  mic     -> in_ring_   (Python reads)
 *   - out_pcm  (playback)         <- out_ring_  (Python writes)
 *   - mon_pcm  (playback, optional) <- mon_ring_ (same denoised audio)
 *   - far_pcm  (capture,  optional) -> far_ring_ (AEC far-end, Python reads)
 *
 * A single I/O thread drives all PCMs via poll(); the data path only
 * touches lock-free SPSC rings (Python thread reads denoise writes).
 *
 * Format: F32 mono 48000Hz negotiated through the plughw converter, so
 * the ALSA plugin layer handles resampling/channel/format conversion
 * (speex/samplerate rate plugin present on the target system).  The
 * model always sees 48k mono.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <unistd.h>
#include <poll.h>
#include <pthread.h>
#include <alsa/asoundlib.h>

#define RING_CAPACITY 4096      /* 4 x hop (85ms) max latency ceiling */
#define PERIOD_FRAMES 1024      /* 20.8ms @ 48k, matches hop */
#define BUFFER_FRAMES 4096

/* ── lock-free SPSC ring (same as pipewire_client.c) ────────────────── */

typedef struct {
    size_t cap_;
    size_t mask_;
    float* buf_;
    size_t w_;          /* producer writes, consumer reads (atomic access) */
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

/* ── AlsaBridge ─────────────────────────────────────────────────────── */

typedef struct AlsaBridge {
    SPSCRing in_ring_;
    SPSCRing out_ring_;
    SPSCRing mon_ring_;
    SPSCRing far_ring_;
    snd_pcm_t* in_pcm_;
    snd_pcm_t* out_pcm_;
    snd_pcm_t* mon_pcm_;
    snd_pcm_t* far_pcm_;
    pthread_t iothread_;
    int thread_started_;
    int running_;        /* atomic bool */
    uint32_t rate_;
    uint32_t period_;
    char last_error_[256];
} AlsaBridge;

/* ── PCM helpers ────────────────────────────────────────────────────── */

static void set_last_error(AlsaBridge* self, const char* msg) {
    snprintf(self->last_error_, sizeof(self->last_error_), "%s", msg);
}

static snd_pcm_t* open_pcm(AlsaBridge* self, const char* name, int stream) {
    snd_pcm_t* pcm = NULL;
    int err = snd_pcm_open(&pcm, name, stream, 0); /* blocking mode */
    if (err < 0) {
        set_last_error(self, name);
        return NULL;
    }

    snd_pcm_hw_params_t* hw;
    snd_pcm_hw_params_alloca(&hw);
    if ((err = snd_pcm_hw_params_any(pcm, hw)) < 0 ||
        (err = snd_pcm_hw_params_set_access(pcm, hw,
                 SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
        (err = snd_pcm_hw_params_set_format(pcm, hw,
                 SND_PCM_FORMAT_FLOAT_LE)) < 0 ||
        (err = snd_pcm_hw_params_set_channels(pcm, hw, 1)) < 0 ||
        (err = snd_pcm_hw_params_set_rate(pcm, hw, 48000, 0)) < 0 ||
        (err = snd_pcm_hw_params_set_period_size_near(pcm, hw,
                 &(snd_pcm_uframes_t){ PERIOD_FRAMES }, NULL)) < 0 ||
        (err = snd_pcm_hw_params_set_buffer_size_near(pcm, hw,
                 &(snd_pcm_uframes_t){ BUFFER_FRAMES })) < 0 ||
        (err = snd_pcm_hw_params(pcm, hw)) < 0) {
        set_last_error(self, snd_strerror(err));
        snd_pcm_close(pcm);
        return NULL;
    }
    if ((err = snd_pcm_prepare(pcm)) < 0) {
        set_last_error(self, snd_strerror(err));
        snd_pcm_close(pcm);
        return NULL;
    }
    /* capture streams must be explicitly started to deliver data to poll() */
    if (stream == SND_PCM_STREAM_CAPTURE && (err = snd_pcm_start(pcm)) < 0) {
        set_last_error(self, snd_strerror(err));
        snd_pcm_close(pcm);
        return NULL;
    }
    return pcm;
}

static int recover_pcm(snd_pcm_t* pcm, int err) {
    if (err == -EPIPE) return snd_pcm_recover(pcm, err, 0);
    if (err == -ESTRPIPE) {
        snd_pcm_recover(pcm, err, 0);
        return 1; /* paused waiting for resume; caller should skip */
    }
    if (err == -EAGAIN) return 1; /* retry later */
    return err;
}

/* I/O thread: poll all open PCMs, move data between rings and PCMs. */
static void* iothread_fn(void* user) {
    AlsaBridge* self = (AlsaBridge*)user;
    static float fbuf[PERIOD_FRAMES];

    /* One pollfd per PCM (capture wants POLLIN, playback POLLOUT). */
    while (__atomic_load_n(&self->running_, __ATOMIC_ACQUIRE)) {
        struct pollfd pfds[4];
        snd_pcm_t* pcms[4];
        int n = 0;
        if (self->in_pcm_)  { pcms[n] = self->in_pcm_;  snd_pcm_poll_descriptors(self->in_pcm_,  &pfds[n], 1); pfds[n].events = POLLIN;  n++; }
        if (self->far_pcm_) { pcms[n] = self->far_pcm_; snd_pcm_poll_descriptors(self->far_pcm_, &pfds[n], 1); pfds[n].events = POLLIN;  n++; }
        if (self->out_pcm_) { pcms[n] = self->out_pcm_; snd_pcm_poll_descriptors(self->out_pcm_, &pfds[n], 1); pfds[n].events = POLLOUT; n++; }
        if (self->mon_pcm_) { pcms[n] = self->mon_pcm_; snd_pcm_poll_descriptors(self->mon_pcm_, &pfds[n], 1); pfds[n].events = POLLOUT; n++; }
        if (n == 0) break;

        int pr = poll(pfds, (nfds_t)n, 100);
        if (pr < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (pr == 0) continue;

        for (int i = 0; i < n; ++i) {
            if (!(pfds[i].revents & (POLLIN | POLLOUT | POLLERR | POLLHUP)))
                continue;
            /* capture: read into ring */
            if (pcms[i] == self->in_pcm_ || pcms[i] == self->far_pcm_) {
                snd_pcm_sframes_t got = snd_pcm_readi(pcms[i], fbuf, PERIOD_FRAMES);
                if (got > 0) {
                    SPSCRing* ring = (pcms[i] == self->in_pcm_)
                                     ? &self->in_ring_ : &self->far_ring_;
                    spsc_write_drop_new(ring, fbuf, (size_t)got);
                } else if (got < 0 && recover_pcm(pcms[i], (int)got) < 0) {
                    /* xrun recover failed; treat as silence continue */
                }
            }
            /* playback: read from ring into PCM */
            else if (pcms[i] == self->out_pcm_ || pcms[i] == self->mon_pcm_) {
                SPSCRing* ring = (pcms[i] == self->out_pcm_)
                                 ? &self->out_ring_ : &self->mon_ring_;
                spsc_read_or_silence(ring, fbuf, PERIOD_FRAMES);
                snd_pcm_sframes_t w = snd_pcm_writei(pcms[i], fbuf, PERIOD_FRAMES);
                if (w < 0 && recover_pcm(pcms[i], (int)w) < 0) {
                    /* continue */
                }
            }
        }
    }
    return NULL;
}

/* ── C API (ctypes via pvalsa.py) ───────────────────────────────────── */

void als_close(AlsaBridge* self);

AlsaBridge* als_new(void) {
    AlsaBridge* self = (AlsaBridge*)calloc(1, sizeof(AlsaBridge));
    if (!self) return NULL;
    spsc_init(&self->in_ring_, RING_CAPACITY);
    spsc_init(&self->out_ring_, RING_CAPACITY);
    spsc_init(&self->mon_ring_, RING_CAPACITY);
    spsc_init(&self->far_ring_, RING_CAPACITY);
    if (!self->in_ring_.buf_ || !self->out_ring_.buf_ ||
        !self->mon_ring_.buf_ || !self->far_ring_.buf_) {
        spsc_free(&self->in_ring_);
        spsc_free(&self->out_ring_);
        spsc_free(&self->mon_ring_);
        spsc_free(&self->far_ring_);
        free(self);
        return NULL;
    }
    self->rate_ = 48000;
    self->period_ = PERIOD_FRAMES;
    self->last_error_[0] = '\0';
    return self;
}

void als_free(AlsaBridge* self) {
    if (!self) return;
    als_close(self);
    spsc_free(&self->in_ring_);
    spsc_free(&self->out_ring_);
    spsc_free(&self->mon_ring_);
    spsc_free(&self->far_ring_);
    free(self);
}

int als_open(AlsaBridge* self, const char* input_name, const char* output_name,
             const char* monitor_name) {
    if (self->in_pcm_ || self->out_pcm_) return 1;
    self->last_error_[0] = '\0';
    if (!input_name || !input_name[0]) {
        set_last_error(self, "missing input device");
        return 0;
    }
    self->in_pcm_ = open_pcm(self, input_name, SND_PCM_STREAM_CAPTURE);
    if (!self->in_pcm_) return 0;
    /* output is optional (ALSA hybrid: denoised audio to virtual mic goes via
       PipeWire native stream; out_pcm then only serves physical monitor) */
    if (output_name && output_name[0]) {
        self->out_pcm_ = open_pcm(self, output_name, SND_PCM_STREAM_PLAYBACK);
        if (!self->out_pcm_) {
            snd_pcm_close(self->in_pcm_);
            self->in_pcm_ = NULL;
            return 0;
        }
    }
    if (monitor_name && monitor_name[0]) {
        self->mon_pcm_ = open_pcm(self, monitor_name, SND_PCM_STREAM_PLAYBACK);
        if (!self->mon_pcm_) {
            /* monitor is optional: keep running without it */
            set_last_error(self, "");
        }
    }

    __atomic_store_n(&self->running_, 1, __ATOMIC_RELEASE);
    self->thread_started_ = 0;
    if (pthread_create(&self->iothread_, NULL, &iothread_fn, self) == 0) {
        self->thread_started_ = 1;
    } else {
        set_last_error(self, "iothread create failed");
    }
    return 1;
}

void als_close(AlsaBridge* self) {
    __atomic_store_n(&self->running_, 0, __ATOMIC_RELEASE);
    if (self->thread_started_) {
        pthread_join(self->iothread_, NULL);
        self->thread_started_ = 0;
        self->iothread_ = 0;
    }
    /* capture 流必须用 snd_pcm_drop（立即丢弃）——snd_pcm_drain 在 capture 上
       会无限阻塞等待剩余数据（实测 pcm.pulse 采集 stop 卡死）；playback 用 drain
       等剩余播放完是安全的。 */
    if (self->in_pcm_)  { snd_pcm_drop(self->in_pcm_);  snd_pcm_close(self->in_pcm_);  self->in_pcm_ = NULL; }
    if (self->out_pcm_) { snd_pcm_drain(self->out_pcm_); snd_pcm_close(self->out_pcm_); self->out_pcm_ = NULL; }
    if (self->mon_pcm_) { snd_pcm_drain(self->mon_pcm_); snd_pcm_close(self->mon_pcm_); self->mon_pcm_ = NULL; }
    if (self->far_pcm_) { snd_pcm_drop(self->far_pcm_);  snd_pcm_close(self->far_pcm_);  self->far_pcm_ = NULL; }
}

int als_active(const AlsaBridge* self) {
    return self->in_pcm_ != NULL;
}

const char* als_last_error(const AlsaBridge* self) {
    return self->last_error_;
}

uint32_t als_sample_rate(const AlsaBridge* self) {
    return self->rate_;
}

uint32_t als_buffer_size(const AlsaBridge* self) {
    return self->period_;
}

int als_set_monitor(AlsaBridge* self, const char* monitor_name, int enabled) {
    if (!self->in_pcm_) return 0;
    if (enabled && !self->mon_pcm_ && monitor_name && monitor_name[0]) {
        snd_pcm_t* pcm = open_pcm(self, monitor_name, SND_PCM_STREAM_PLAYBACK);
        if (!pcm) return 0;
        self->mon_pcm_ = pcm;
        return 1;
    }
    if (!enabled && self->mon_pcm_) {
        snd_pcm_t* pcm = self->mon_pcm_;
        self->mon_pcm_ = NULL;
        snd_pcm_drain(pcm);
        snd_pcm_close(pcm);
        return 1;
    }
    return 1;
}

int als_set_far(AlsaBridge* self, const char* far_name, int enabled) {
    if (!self->in_pcm_) return 0;
    if (enabled && !self->far_pcm_ && far_name && far_name[0]) {
        snd_pcm_t* pcm = open_pcm(self, far_name, SND_PCM_STREAM_CAPTURE);
        if (!pcm) return 0;
        self->far_pcm_ = pcm;
        return 1;
    }
    if (!enabled && self->far_pcm_) {
        snd_pcm_t* pcm = self->far_pcm_;
        self->far_pcm_ = NULL;
        snd_pcm_drain(pcm);
        snd_pcm_close(pcm);
        return 1;
    }
    return 1;
}

size_t als_read(AlsaBridge* self, float* out, size_t n) {
    return spsc_read(&self->in_ring_, out, n);
}

size_t als_read_far(AlsaBridge* self, float* out, size_t n) {
    return spsc_read(&self->far_ring_, out, n);
}

void als_write(AlsaBridge* self, const float* data, size_t n) {
    if (n == 0) return;
    if (self->out_pcm_) spsc_write_drop_new(&self->out_ring_, data, n);
    if (self->mon_pcm_) spsc_write_drop_new(&self->mon_ring_, data, n);
}