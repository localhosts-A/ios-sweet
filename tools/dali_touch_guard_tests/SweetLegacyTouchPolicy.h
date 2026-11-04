/* Policy for the legacy Qualcomm SWEET touch path.  No MMIO. */
#ifndef SWEET_LEGACY_TOUCH_POLICY_H
#define SWEET_LEGACY_TOUCH_POLICY_H

enum sweet_legacy_touch_decision {
    SWEET_LEGACY_TOUCH_ALLOW = 0,
    SWEET_LEGACY_TOUCH_DISABLED,
    SWEET_LEGACY_TOUCH_DALI,
    SWEET_LEGACY_TOUCH_UNKNOWN_BOARD
};

/* The root compatible property is a bounded NUL-separated string list.
 * Refuse malformed or unavailable identity rather than risk wrong-SoC MMIO.
 * A Dali token anywhere in a valid list disables this path, even if the
 * legacy boot argument explicitly requests it.
 */
static inline enum sweet_legacy_touch_decision
sweet_legacy_touch_policy(int enabled, int have_compatible,
    const void *value, unsigned int size)
{
    static const unsigned char target[] = "xiaomi,dali";
    const unsigned char *p = (const unsigned char *)value;
    int dali = 0, saw_token = 0;
    unsigned int i = 0;

    if (!enabled) return SWEET_LEGACY_TOUCH_DISABLED;
    if (!have_compatible || !p || !size) return SWEET_LEGACY_TOUCH_UNKNOWN_BOARD;
    while (i < size) {
        unsigned int start = i, j;
        while (i < size && p[i] != 0) ++i;
        if (i == size) return SWEET_LEGACY_TOUCH_UNKNOWN_BOARD;
        if (i != start) saw_token = 1;
        if (i - start == sizeof(target) - 1) {
            for (j = 0; j < sizeof(target) - 1; ++j) {
                if (p[start + j] != target[j]) break;
            }
            if (j == sizeof(target) - 1) dali = 1;
        }
        ++i;
    }
    if (!saw_token) return SWEET_LEGACY_TOUCH_UNKNOWN_BOARD;
    return dali ? SWEET_LEGACY_TOUCH_DALI : SWEET_LEGACY_TOUCH_ALLOW;
}
#endif
