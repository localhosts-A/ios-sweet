#include "SweetLegacyTouchPolicy.h"
#include <assert.h>
#include <stdio.h>

static unsigned starts;
static void simulate_init(int enabled, int have, const void *value, unsigned size) {
    if (sweet_legacy_touch_policy(enabled, have, value, size) == SWEET_LEGACY_TOUCH_ALLOW)
        ++starts; /* stands for kernel_thread_start; MMIO lies inside the thread */
}
int main(void) {
    static const char dali[] = "xiaomi,sweet\0xiaomi,dali\0mediatek,mt6991";
    static const char sweet[] = "xiaomi,sweet\0qcom,sm6150";
    static const char similar[] = "xiaomi,dali2\0vendor,xiaomi,dali";
    static const char broken[] = {'x','i','a','o','m','i',',','d','a','l','i'};
    static const char empty[] = {0};
    starts = 0;
    /* Missing boot argument means existing enabled default of one. */
    simulate_init(1, 1, dali, sizeof(dali));
    simulate_init(1, 1, dali, sizeof(dali)); /* explicit sweettouch=1 */
    simulate_init(0, 1, dali, sizeof(dali));
    assert(starts == 0);
    assert(sweet_legacy_touch_policy(1, 1, dali, sizeof(dali)) == SWEET_LEGACY_TOUCH_DALI);
    assert(sweet_legacy_touch_policy(0, 1, sweet, sizeof(sweet)) == SWEET_LEGACY_TOUCH_DISABLED);
    simulate_init(1, 1, sweet, sizeof(sweet));
    simulate_init(1, 1, similar, sizeof(similar));
    simulate_init(0, 1, sweet, sizeof(sweet));
    assert(starts == 2); /* normal valid non-Dali default/on/off behavior */
    assert(sweet_legacy_touch_policy(1, 0, sweet, sizeof(sweet)) == SWEET_LEGACY_TOUCH_UNKNOWN_BOARD);
    assert(sweet_legacy_touch_policy(1, 1, NULL, 3) == SWEET_LEGACY_TOUCH_UNKNOWN_BOARD);
    assert(sweet_legacy_touch_policy(1, 1, broken, sizeof(broken)) == SWEET_LEGACY_TOUCH_UNKNOWN_BOARD);
    assert(sweet_legacy_touch_policy(1, 1, empty, sizeof(empty)) == SWEET_LEGACY_TOUCH_UNKNOWN_BOARD);
    assert(sweet_legacy_touch_policy(1, 1, dali, sizeof(dali)-1) == SWEET_LEGACY_TOUCH_UNKNOWN_BOARD);
    puts("legacy touch policy fixture passed");
    return 0;
}
