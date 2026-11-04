/* Dali MT6991 GPIO60 volume-up input policy; no hardware access here. */
#ifndef DALI_VOLUME_KEY_H
#define DALI_VOLUME_KEY_H

struct DaliVolumeKeyState {
    bool armed;
    bool down;
    bool candidateValid;
    bool candidateDown;
    unsigned long long sinceNs;
};

/* ADT compatible is a sequence of NUL-terminated byte strings. */
static inline bool dali_volume_board_enabled(int optIn, const void *data,
    unsigned int length)
{
    static const char board[] = "xiaomi,dali";
    const unsigned char *bytes = static_cast<const unsigned char *>(data);
    if (optIn != 1 || !bytes || !length) return false;
    bool found = false;
    for (unsigned int i = 0; i < length;) {
        unsigned int start = i;
        while (i < length && bytes[i]) ++i;
        if (i == length) return false;
        if (i - start == sizeof(board) - 1) {
            unsigned int j = 0;
            while (j < sizeof(board) - 1 && bytes[start + j] == (unsigned char)board[j]) ++j;
            if (j == sizeof(board) - 1) found = true;
        }
        ++i;
    }
    return found;
}

/* Pin 60 must still be GPIO input, with input buffer enabled. */
static inline bool dali_volume_pin_valid(unsigned int mode, unsigned int dir,
    unsigned int ies)
{
    return ((mode >> 16) & 0xfU) == 0 && !(dir & (1U << 28)) &&
        (ies & (1U << 19));
}

/* -1 means no event, 1 is a physical down and 0 a physical up.  A ready
 * client must first see a stable release; no pre-client press is replayed.
 * The 32 ms requirement uses elapsed monotonic time, not callback count.
 */
static inline int dali_volume_sample(DaliVolumeKeyState *s, bool clientReady,
    unsigned int dataIn, unsigned long long nowNs)
{
    if (!clientReady) {
        *s = {};
        return -1;
    }
    bool down = (dataIn & (1U << 28)) == 0; /* active low */
    if (!s->candidateValid || s->candidateDown != down || nowNs < s->sinceNs) {
        s->candidateValid = true;
        s->candidateDown = down;
        s->sinceNs = nowNs;
        return -1;
    }
    if (nowNs - s->sinceNs < 32000000ULL) return -1;
    if (!s->armed) {
        if (!down) {
            s->armed = true;
            s->down = false;
        }
        return -1;
    }
    if (s->down == down) return -1;
    s->down = down;
    return down ? 1 : 0;
}
#endif
