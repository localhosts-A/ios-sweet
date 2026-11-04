#include <cassert>
#include <cstdio>
#include "DaliVolumeKey.h"

static constexpr unsigned UP = 1U << 28;
static constexpr unsigned IES = 1U << 19;
static constexpr unsigned long long MS = 1000000ULL;
static int sample(DaliVolumeKeyState &s, bool client, bool high, unsigned long long ms) {
    return dali_volume_sample(&s, client, high ? UP : 0, ms * MS);
}
int main() {
    const char board[] = "D22AP\0xiaomi,sweet\0xiaomi,dali\0mediatek,mt6991\0";
    const char other[] = "xiaomi,sweet\0mediatek,mt6991\0";
    const char similar[] = "xiaomi,dalix\0";
    const char malformed[] = {'x','i','a','o','m','i',',','d','a','l','i'};
    assert(dali_volume_board_enabled(1, board, sizeof(board)));
    assert(!dali_volume_board_enabled(0, board, sizeof(board)));
    assert(!dali_volume_board_enabled(2, board, sizeof(board)));
    assert(!dali_volume_board_enabled(1, other, sizeof(other)));
    assert(!dali_volume_board_enabled(1, similar, sizeof(similar)));
    assert(!dali_volume_board_enabled(1, malformed, sizeof(malformed)));
    assert(!dali_volume_board_enabled(1, nullptr, 99));
    assert(!dali_volume_board_enabled(1, board, 0));
    assert(dali_volume_pin_valid(0, 0, IES));
    assert(dali_volume_pin_valid(0xff00ffffU, ~UP, ~0U));
    assert(!dali_volume_pin_valid(1U << 16, 0, IES));
    assert(!dali_volume_pin_valid(0, UP, IES));
    assert(!dali_volume_pin_valid(0, 0, 0));
    assert(dali_volume_pin_rejects(0, 0, IES) == 0);
    assert(dali_volume_pin_rejects(1U << 16, 0, IES) == DALI_VOLUME_PIN_MODE_INVALID);
    assert(dali_volume_pin_rejects(0, UP, IES) == DALI_VOLUME_PIN_DIRECTION_OUTPUT);
    assert(dali_volume_pin_rejects(0, 0, 0) == DALI_VOLUME_PIN_IES_DISABLED);
    assert(dali_volume_pin_rejects(1U << 16, UP, 0) ==
        (DALI_VOLUME_PIN_MODE_INVALID | DALI_VOLUME_PIN_DIRECTION_OUTPUT | DALI_VOLUME_PIN_IES_DISABLED));

    DaliVolumeKeyState s = {};
    /* No client, then connect while held: never invent a late down. */
    assert(sample(s, false, false, 0) == -1);
    assert(sample(s, true, false, 10) == -1);
    assert(sample(s, true, false, 50) == -1 && !s.armed);
    assert(sample(s, true, true, 60) == -1);
    assert(sample(s, true, true, 91) == -1 && !s.armed);
    assert(sample(s, true, true, 92) == -1 && s.armed && !s.down);
    /* Bounce does not reach 32 ms. */
    assert(sample(s, true, false, 100) == -1);
    assert(sample(s, true, true, 120) == -1);
    assert(sample(s, true, false, 125) == -1);
    assert(sample(s, true, false, 156) == -1);
    assert(sample(s, true, false, 157) == 1 && s.down);
    assert(sample(s, true, false, 1000) == -1);
    assert(sample(s, true, true, 1001) == -1);
    assert(sample(s, true, true, 1032) == -1);
    assert(sample(s, true, true, 1033) == 0 && !s.down);
    assert(sample(s, true, true, 2000) == -1);
    /* A disconnected client cannot receive events; rearm requires release. */
    assert(sample(s, true, false, 2001) == -1);
    assert(sample(s, true, false, 2033) == 1);
    assert(sample(s, false, false, 2040) == -1 && !s.armed);
    assert(sample(s, true, false, 2050) == -1);
    assert(sample(s, true, false, 2100) == -1 && !s.armed);
    assert(sample(s, true, true, 2110) == -1);
    assert(sample(s, true, true, 2142) == -1 && s.armed);
    /* Clock reversal restarts candidate time, never underflows into an event. */
    assert(sample(s, true, false, 2200) == -1);
    assert(sample(s, true, false, 2199) == -1);
    assert(sample(s, true, false, 2230) == -1);
    assert(sample(s, true, false, 2231) == 1);
    assert(sample(s, true, true, 2300) == -1);
    assert(sample(s, true, true, 2332) == 0);
    std::puts("volume policy: all cases passed");
}
