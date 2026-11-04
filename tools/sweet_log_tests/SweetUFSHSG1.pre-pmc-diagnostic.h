/* Private functional integration. Platform helper must adopt an actual retained
 * AP floor and prove the already-running clock; neither argument nor ADT cap
 * can substitute for that implementation. No PLL/PMIC/DVFS INIT is done here.
 */
static int ufs_dali_hs_platform_adopt(void);
static int ufs_dali_hs_platform_validate(void);

static int
ufs_dali_prepare_hsg1(bool requested)
{
    if (!requested || !su.dali) return 0;
    int error = ufs_dali_hs_platform_adopt();
    if (error) return error;
    uint64_t span = 0, now = mach_absolute_time();
    nanoseconds_to_absolutetime(30ull * NSEC_PER_SEC, &span);
    if (span == 0 || now > UINT64_MAX - span) return ufs_pwm_latch(ETIMEDOUT, false);
    uint64_t until = now + span;
    struct sweet_pwm_snapshot before = {0}, after = {0};
    uint32_t local_max = 0, peer_max = 0, series = 0;
    error = ufs_pwm_snapshot(&before, until);
    if (error) return ufs_pwm_failure(error);
    if ((before.mode != 0x55 && before.mode != 0x22) ||
        before.rx_gear != 1 || before.tx_gear != 1 ||
        before.rx_active != 1 || before.tx_active != 1 ||
        before.rx_term != 0 || before.tx_term != 0) return ENOTSUP;
    error = ufs_pwm_get(false, 0x1587, &local_max, until); /* PA_MAXRXHSGEAR */
    if (!error) error = ufs_pwm_get(true, 0x1587, &peer_max, until);
    if (error) return ufs_pwm_failure(error);
    printf("SWEET UFS HSG1: before mode=0x%x gear=%u/%u lanes=%u/%u hsmax=%u/%u\n",
        before.mode, before.rx_gear, before.tx_gear, before.rx_active, before.tx_active,
        local_max, peer_max);
    if (local_max == 0 || peer_max == 0) return ENOTSUP;
    if (local_max > 5 || peer_max > 5) return ufs_pwm_latch(EIO, false);
    error = ufs_dali_hs_platform_validate();
    if (error) return error; /* No SET has been submitted. */
    /* Fixed vendor/core low-gear contract: single lane, HS-G1, rate B,
     * FASTAUTO (not FAST/G4+, so the optional intermediate PMC is inapplicable),
     * termination enabled and NO_ADAPT=3. Keep original UniPro timeouts.
     */
    const uint32_t attributes[] = {
        0x15d4, 0x1583, 0x1580, 0x1584, 0x1568, 0x1560, 0x1569, 0x156a,
        0x15b0, 0x15b1, 0x15b2, 0x15b3, 0x15b4, 0x15b5,
        0xd041, 0xd042, 0xd043
    };
    const uint32_t values[] = {
        3, 1, 1, 1, 1, 1, 1, 2,
        8191, 65535, 32767, 8191, 65535, 32767, 8191, 65535, 32767
    };
    _Static_assert(sizeof(attributes) == sizeof(values), "HS attribute/value mismatch");
    for (unsigned int i = 0; i < sizeof(attributes) / sizeof(attributes[0]); ++i) {
        error = ufs_dali_hs_platform_validate();
        if (error) return ufs_pwm_failure(error); /* Staged configuration: no fallback. */
        error = ufs_pwm_set(attributes[i], values[i], until);
        if (error) return ufs_pwm_failure(error);
    }
    error = ufs_dali_hs_platform_validate();
    if (!error) error = ufs_pwm_pmc(0x44, until); /* Separate async UCCS + UPMS. */
    if (!error) error = ufs_pwm_snapshot(&after, until);
    if (!error) error = ufs_pwm_get(false, 0x156a, &series, until);
    if (error) return ufs_pwm_failure(error);
    if (after.mode != 0x44 || after.rx_gear != 1 || after.tx_gear != 1 ||
        after.rx_active != 1 || after.tx_active != 1 || series != 2 ||
        after.rx_term != 1 || after.tx_term != 1 ||
        after.rx_connected != before.rx_connected || after.tx_connected != before.tx_connected)
        return ufs_pwm_latch(EIO, false);
    error = ufs_dali_hs_platform_validate();
    if (error) return ufs_pwm_failure(error);
    printf("SWEET UFS HSG1: verified mode=0x44 gear=1/1 lanes=1/1 series=B readback=1\n");
    return 0;
}

/* G4x2 is an isolated, read-only probe candidate, not a voltage or signal
 * integrity guarantee. Linux's MT6991 DT requests a 650mV VCORE minimum; the
 * existing platform code retains that AP policy and validates its clock.
 * Neither request/completed selector nor firmware calibration proves rail ADC.
 * Current PWM has no active HS series (Linux initializes hs_rate to zero), so
 * always take the vendor's series-change G1B FASTAUTO intermediate. No retry,
 * same-boot rollback, automatic G5, or fallback to a lower gear is permitted.
 */
/* The context is set before each existing operation. Report only the first
 * failing operation, without extra UIC commands or speculative recovery.
 * The retained console may drop chunks, so a missing line is not proof that a
 * command or phase did not run. A range is inclusive; "pwm-mode" means 0x22
 * or 0x55, not all intervening values. */
static void
ufs_hsg4_context(const char *operation, const char *endpoint, uint32_t attribute,
    const char *expectation, bool expected_valid, uint32_t low, uint32_t high)
{
    sweet_hsg4_diag.operation = operation;
    sweet_hsg4_diag.endpoint = endpoint;
    sweet_hsg4_diag.attribute = attribute;
    sweet_hsg4_diag.expectation = expectation;
    sweet_hsg4_diag.expected_valid = expected_valid;
    sweet_hsg4_diag.expected_min = low;
    sweet_hsg4_diag.expected_max = high;
    sweet_hsg4_diag.observed_valid = false;
    sweet_hsg4_diag.observed = 0;
}

static void
ufs_hsg4_compare(bool peer, uint32_t attr, const char *expectation,
    uint32_t low, uint32_t high, uint32_t observed)
{
    ufs_hsg4_context("compare", peer ? "peer" : "local", attr,
        expectation, true, low, high);
    sweet_hsg4_diag.observed = observed;
    sweet_hsg4_diag.observed_valid = true;
}

static int
ufs_hsg4_get(bool peer, uint32_t attr, uint32_t *value, uint64_t until,
    const char *operation, const char *expectation, bool expected_valid,
    uint32_t low, uint32_t high)
{
    ufs_hsg4_context(operation, peer ? "peer" : "local", attr,
        expectation, expected_valid, low, high);
    int error = ufs_pwm_get(peer, attr, value, until);
    if (!error) {
        sweet_hsg4_diag.observed = *value;
        sweet_hsg4_diag.observed_valid = true;
    }
    return error;
}

static int
ufs_hsg4_set(bool peer, uint32_t attr, uint32_t value, uint64_t until)
{
    ufs_hsg4_context("set", peer ? "peer" : "local", attr, "eq", true, value, value);
    return peer ? ufs_pwm_peer_set(attr, value, until) : ufs_pwm_set(attr, value, until);
}

static int
ufs_hsg4_floor(uint32_t next_attribute)
{
    /* Attribute identifies the next intended UIC operation, not a DVFS register. */
    ufs_hsg4_context("floor-before", "platform", next_attribute, "none", false, 0, 0);
    return ufs_dali_hs_platform_validate();
}

static void
ufs_hsg4_report(int error)
{
    if (!sweet_hsg4_diag.first_error_valid) {
        sweet_hsg4_diag.first_error = error;
        sweet_hsg4_diag.first_error_valid = true;
    }
    error = sweet_hsg4_diag.first_error;
    printf("SWEET UFS HSG4 FAIL: v=1 error=%d phase=%s op=%s endpoint=%s attr=0x%x "
        "expect=%s expected-valid=%u expected-min=0x%x expected-max=0x%x "
        "observed-valid=%u observed=0x%x repeat=%u\n", error,
        sweet_hsg4_diag.phase, sweet_hsg4_diag.operation, sweet_hsg4_diag.endpoint,
        sweet_hsg4_diag.attribute, sweet_hsg4_diag.expectation,
        sweet_hsg4_diag.expected_valid ? 1u : 0u, sweet_hsg4_diag.expected_min,
        sweet_hsg4_diag.expected_max, sweet_hsg4_diag.observed_valid ? 1u : 0u,
        sweet_hsg4_diag.observed, sweet_hsg4_diag.terminal_reemitted ? 1u : 0u);
    printf("SWEET UFS HSG4 PMC: submit=0x%x complete=0x%x ack=0x%x idle=0x%x "
        "mode=0x%x stage=%s error=%d polls=%u sampled=%u IS=0x%x HCS=0x%x arg2-valid=%u "
        "ARG2=0x%x uccs=%u upms=%u ack-valid=%u ack-IS=0x%x\n",
        sweet_hsg4_diag.pmc_submitted, sweet_hsg4_diag.pmc_completed,
        sweet_hsg4_diag.pmc_acknowledged, sweet_hsg4_diag.pmc_idle_ok,
        sweet_hsg4_diag.pmc_mode,
        sweet_hsg4_diag.pmc_failure_stage ? sweet_hsg4_diag.pmc_failure_stage : "none",
        sweet_hsg4_diag.pmc_error, sweet_hsg4_diag.pmc_polls,
        sweet_hsg4_diag.pmc_sampled ? 1u : 0u, sweet_hsg4_diag.pmc_status,
        sweet_hsg4_diag.pmc_hcs, sweet_hsg4_diag.pmc_arg2_valid ? 1u : 0u,
        sweet_hsg4_diag.pmc_arg2, sweet_hsg4_diag.pmc_uccs ? 1u : 0u,
        sweet_hsg4_diag.pmc_upms ? 1u : 0u,
        sweet_hsg4_diag.pmc_ack_status_valid ? 1u : 0u, sweet_hsg4_diag.pmc_ack_status);
}

/* The caller has already observed an HS read/compare error after the G4
 * transition returned success. Do not infer which suboperation caused it. */
static void
ufs_hsg4_post_probe_error(int error)
{
    if (sweet_hsg4_diag.first_error_valid) return;
    sweet_hsg4_diag.phase = "post-transition-probe";
    ufs_hsg4_context(su.failure_stage ? su.failure_stage : "unknown", "probe", 0,
        "none", false, 0, 0);
    ufs_hsg4_report(error);
}

/* Only used after an existing snapshot comparison has failed. */
static void
ufs_hsg4_snapshot_mismatch(bool peer, const struct sweet_pwm_snapshot *s,
    uint32_t mode, uint32_t gear, uint32_t lanes, uint32_t term)
{
    const uint32_t attrs[] = {0x1571, 0x1583, 0x1568, 0x1580, 0x1560,
        0x1581, 0x1561, 0x1584, 0x1569};
    const uint32_t actual[] = {s->mode, s->rx_gear, s->tx_gear, s->rx_active,
        s->tx_active, s->rx_connected, s->tx_connected, s->rx_term, s->tx_term};
    const uint32_t expected[] = {mode, gear, gear, lanes, lanes, 2, 2, term, term};
    for (unsigned int i = 0; i < sizeof(attrs) / sizeof(attrs[0]); ++i) {
        if (actual[i] != expected[i]) {
            ufs_hsg4_compare(peer, attrs[i], "eq", expected[i], expected[i], actual[i]);
            return;
        }
    }
    /* Defensive: no extra reads, and do not assert a specific mismatch. */
    ufs_hsg4_context("snapshot-invalid", peer ? "peer" : "local", 0, "none", false, 0, 0);
}

static int
ufs_hsg4_snapshot(bool peer, struct sweet_pwm_snapshot *s, uint64_t until,
    uint32_t mode, uint32_t gear, uint32_t lanes, uint32_t term)
{
    const uint32_t attrs[] = {0x1571, 0x1583, 0x1568, 0x1580, 0x1560,
        0x1581, 0x1561, 0x1584, 0x1569};
    uint32_t *values[] = {&s->mode, &s->rx_gear, &s->tx_gear, &s->rx_active,
        &s->tx_active, &s->rx_connected, &s->tx_connected, &s->rx_term, &s->tx_term};
    const uint32_t expected[] = {mode, gear, gear, lanes, lanes, 2, 2, term, term};
    for (unsigned int i = 0; i < sizeof(attrs) / sizeof(attrs[0]); ++i) {
        bool pwm_mode = i == 0 && mode == 0;
        int error = ufs_hsg4_get(peer, attrs[i], values[i], until, "snapshot-get",
            pwm_mode ? "pwm-mode" : "eq", true,
            pwm_mode ? 0x22 : expected[i], pwm_mode ? 0x55 : expected[i]);
        if (error) return error;
    }
    if (sweet_pwm_snapshot_valid(s)) return 0;
    if (mode == 0 && s->mode != 0x22 && s->mode != 0x55)
        ufs_hsg4_compare(peer, 0x1571, "pwm-mode", 0x22, 0x55, s->mode);
    else ufs_hsg4_snapshot_mismatch(peer, s, mode ? mode : s->mode, gear, lanes, term);
    return EIO;
}

static bool
ufs_hsg4_matches(const struct sweet_pwm_snapshot *s, uint32_t mode,
    uint32_t gear, uint32_t lanes, uint32_t term)
{
    return s->mode == mode && s->rx_gear == gear && s->tx_gear == gear &&
        s->rx_active == lanes && s->tx_active == lanes &&
        s->rx_connected == 2 && s->tx_connected == 2 &&
        s->rx_term == term && s->tx_term == term;
}

/* Require real runtime GETs on both ends; connected lanes alone are not AVAIL. */
static int
ufs_hsg4_capabilities(uint64_t until, uint32_t max_gear[2])
{
    for (unsigned int peer = 0; peer < 2; ++peer) {
        uint32_t rx = 0, tx = 0, unipro_version = 0;
        int error = ufs_hsg4_get(peer != 0, 0x1540, &rx, until, "cap-get", "eq", true, 2, 2);
        if (!error) error = ufs_hsg4_get(peer != 0, 0x1520, &tx, until, "cap-get", "eq", true, 2, 2);
        if (!error) error = ufs_hsg4_get(peer != 0, 0x1587, &max_gear[peer], until, "cap-get", "range", true, 4, 5);
        if (!error) error = ufs_hsg4_get(peer != 0, 0x15a9, &unipro_version, until, "cap-get", "range", true, 5, 6);
        if (error) return error;
        sweet_hsg4_diag.avail_rx[peer] = rx;
        sweet_hsg4_diag.avail_tx[peer] = tx;
        sweet_hsg4_diag.unipro_version[peer] = unipro_version;
        if (rx != 2 || tx != 2 || max_gear[peer] < 4 || max_gear[peer] > 5 ||
            (unipro_version & ~0xfu) != 0 || unipro_version < 5 || unipro_version > 6) {
            if (rx != 2) ufs_hsg4_compare(peer != 0, 0x1540, "eq", 2, 2, rx);
            else if (tx != 2) ufs_hsg4_compare(peer != 0, 0x1520, "eq", 2, 2, tx);
            else if (max_gear[peer] < 4 || max_gear[peer] > 5)
                ufs_hsg4_compare(peer != 0, 0x1587, "range", 4, 5, max_gear[peer]);
            else ufs_hsg4_compare(peer != 0, 0x15a9, "range", 5, 6, unipro_version);
            return ENOTSUP;
        }
    }
    return 0;
}

/* All ten sync lengths are read at each endpoint. Preserve values already
 * above 0x48; check the exact post-SET value and again after final PMC. */
static int
ufs_hsg4_sync(uint64_t until, uint32_t expected[2][5], bool program)
{
    static const uint32_t attrs[] = {0x1552, 0x1554, 0x1556, 0x15d0, 0x15d6};
    sweet_hsg4_diag.phase = program ? "sync-prepare" : "sync-final";
    for (unsigned int i = 0; i < 5; ++i) {
        for (unsigned int peer = 0; peer < 2; ++peer) {
            uint32_t value = 0;
            int error = ufs_hsg4_get(peer != 0, attrs[i], &value, until, "sync-get",
                program ? "none" : "eq", !program, expected[peer][i], expected[peer][i]);
            if (error) return error;
            if (program) {
                expected[peer][i] = value < 0x48 ? 0x48 : value;
                if (value < 0x48) {
                    error = ufs_hsg4_floor(attrs[i]);
                    if (!error) error = ufs_hsg4_set(peer != 0, attrs[i], 0x48, until);
                    if (!error) error = ufs_hsg4_get(peer != 0, attrs[i], &value, until,
                        "sync-readback", "eq", true, 0x48, 0x48);
                    if (error) return error;
                }
            }
            if (value != expected[peer][i] || value < 0x48) {
                ufs_hsg4_compare(peer != 0, attrs[i], "eq", expected[peer][i], expected[peer][i], value);
                return EIO;
            }
        }
    }
    return 0;
}

/* Stage the vendor timeout defaults plus symmetric link attributes. Verify
 * the eight critical staged settings before issuing exactly one PMC. */
static int
ufs_hsg4_stage(uint64_t until, uint32_t gear, uint32_t mode)
{
    if (!((gear == 1 && mode == 0x44) || (gear == 4 && mode == 0x11))) return EINVAL;
    const uint32_t attrs[] = {0x15d4, 0x1583, 0x1580, 0x1584, 0x1568,
        0x1560, 0x1569, 0x156a, 0x15b0, 0x15b1, 0x15b2, 0x15b3,
        0x15b4, 0x15b5, 0xd041, 0xd042, 0xd043};
    const uint32_t values[] = {gear >= 4 ? 1u : 3u, gear, 2, 1, gear,
        2, 1, 2, 8191, 65535, 32767, 8191, 65535, 32767, 8191, 65535, 32767};
    _Static_assert(sizeof(attrs) == sizeof(values), "HS G4 attribute/value mismatch");
    sweet_hsg4_diag.phase = gear == 1 ? "stage-g1" : "stage-g4";
    for (unsigned int i = 0; i < sizeof(attrs) / sizeof(attrs[0]); ++i) {
        int error = ufs_hsg4_floor(attrs[i]);
        if (!error) error = ufs_hsg4_set(false, attrs[i], values[i], until);
        if (error) return error;
    }
    for (unsigned int i = 0; i < 8; ++i) {
        uint32_t value = 0;
        int error = ufs_hsg4_get(false, attrs[i], &value, until,
            "staged-get", "eq", true, values[i], values[i]);
        if (error) return error;
        if (value != values[i]) {
            ufs_hsg4_compare(false, attrs[i], "eq", values[i], values[i], value);
            return EIO;
        }
    }
    int error = ufs_hsg4_floor(0x1571);
    if (error) return error;
    ufs_hsg4_context("pmc", "local", 0x1571, "eq", true, mode, mode);
    return ufs_pwm_pmc(mode, until);
}

static int
ufs_hsg4_verify(uint64_t until, uint32_t gear, uint32_t mode)
{
    struct sweet_pwm_snapshot local = {0}, peer = {0};
    uint32_t local_series = 0, peer_series = 0, adapt = 0;
    uint32_t expected_adapt = gear >= 4 ? 1u : 3u;
    sweet_hsg4_diag.phase = gear == 1 ? "verify-g1" : "verify-g4";
    int error = ufs_hsg4_snapshot(false, &local, until, mode, gear, 2, 1);
    if (!error) error = ufs_hsg4_snapshot(true, &peer, until, mode, gear, 2, 1);
    if (!error) error = ufs_hsg4_get(false, 0x156a, &local_series, until, "verify-get", "eq", true, 2, 2);
    if (!error) error = ufs_hsg4_get(true, 0x156a, &peer_series, until, "verify-get", "eq", true, 2, 2);
    if (!error) error = ufs_hsg4_get(false, 0x15d4, &adapt, until, "verify-get", "eq", true, expected_adapt, expected_adapt);
    if (error) return error;
    if (!ufs_hsg4_matches(&local, mode, gear, 2, 1) ||
        !ufs_hsg4_matches(&peer, mode, gear, 2, 1) ||
        local_series != 2 || peer_series != 2 || adapt != expected_adapt) {
        if (!ufs_hsg4_matches(&local, mode, gear, 2, 1))
            ufs_hsg4_snapshot_mismatch(false, &local, mode, gear, 2, 1);
        else if (!ufs_hsg4_matches(&peer, mode, gear, 2, 1))
            ufs_hsg4_snapshot_mismatch(true, &peer, mode, gear, 2, 1);
        else if (local_series != 2) ufs_hsg4_compare(false, 0x156a, "eq", 2, 2, local_series);
        else if (peer_series != 2) ufs_hsg4_compare(true, 0x156a, "eq", 2, 2, peer_series);
        else ufs_hsg4_compare(false, 0x15d4, "eq", expected_adapt, expected_adapt, adapt);
        return EIO;
    }
    return ufs_hsg4_floor(0);
}

static int
ufs_dali_prepare_hsg4(bool requested)
{
    if (!requested || !su.dali) return 0;
    int error = ufs_pwm_reentry_error();
    if (error) return error;
    sweet_hsg4_diag = (struct sweet_hsg4_diagnostic){0};
    sweet_hsg4_diag.phase = "adopt";
    ufs_hsg4_context("floor-adopt", "platform", 0, "none", false, 0, 0);
    error = ufs_dali_hs_platform_adopt();
    if (error) { ufs_hsg4_report(error); return ufs_pwm_failure(error); }
    uint64_t span = 0, now = mach_absolute_time();
    nanoseconds_to_absolutetime(30ull * NSEC_PER_SEC, &span);
    if (span == 0 || now > UINT64_MAX - span) {
        sweet_hsg4_diag.phase = "deadline";
        ufs_hsg4_context("deadline", "platform", 0, "none", false, 0, 0);
        ufs_hsg4_report(ETIMEDOUT);
        return ufs_pwm_latch(ETIMEDOUT, false);
    }
    uint64_t until = now + span;
    struct sweet_pwm_snapshot local = {0}, peer = {0};
    uint32_t max_gear[2] = {0}, after_max[2] = {0}, local_series = 0, peer_series = 0;
    uint32_t sync[2][5] = {{0}};
    sweet_hsg4_diag.phase = "before-snapshot";
    error = ufs_hsg4_snapshot(false, &local, until, 0, 1, 1, 0);
    if (!error) error = ufs_hsg4_snapshot(true, &peer, until, local.mode, 1, 1, 0);
    if (!error) { sweet_hsg4_diag.phase = "before-capability"; error = ufs_hsg4_capabilities(until, max_gear); }
    if (!error) { sweet_hsg4_diag.phase = "before-series";
        error = ufs_hsg4_get(false, 0x156a, &local_series, until, "series-get", "range", true, 0, 2); }
    if (!error) error = ufs_hsg4_get(true, 0x156a, &peer_series, until, "series-get", "range", true, 0, 2);
    if (error) { ufs_hsg4_report(error); return ufs_pwm_failure(error); }
    printf("SWEET UFS HSG4: before local=0x%x peer=0x%x series=%u/%u hsmax=%u/%u avail=2/2\n",
        local.mode, peer.mode, local_series, peer_series, max_gear[0], max_gear[1]);
    printf("SWEET UFS HSG4 CAP: local-rx=%u local-tx=%u peer-rx=%u peer-tx=%u "
        "local-ver=%u peer-ver=%u\n", sweet_hsg4_diag.avail_rx[0], sweet_hsg4_diag.avail_tx[0],
        sweet_hsg4_diag.avail_rx[1], sweet_hsg4_diag.avail_tx[1],
        sweet_hsg4_diag.unipro_version[0], sweet_hsg4_diag.unipro_version[1]);
    sweet_hsg4_diag.phase = "before-admission";
    if ((local.mode != 0x55 && local.mode != 0x22) ||
        !ufs_hsg4_matches(&local, local.mode, 1, 1, 0) ||
        !ufs_hsg4_matches(&peer, local.mode, 1, 1, 0) ||
        local_series > 2 || peer_series > 2) {
        if (local.mode != 0x55 && local.mode != 0x22)
            ufs_hsg4_compare(false, 0x1571, "pwm-mode", 0x22, 0x55, local.mode);
        else if (!ufs_hsg4_matches(&local, local.mode, 1, 1, 0))
            ufs_hsg4_snapshot_mismatch(false, &local, local.mode, 1, 1, 0);
        else if (!ufs_hsg4_matches(&peer, local.mode, 1, 1, 0))
            ufs_hsg4_snapshot_mismatch(true, &peer, local.mode, 1, 1, 0);
        else if (local_series > 2) ufs_hsg4_compare(false, 0x156a, "range", 0, 2, local_series);
        else ufs_hsg4_compare(true, 0x156a, "range", 0, 2, peer_series);
        ufs_hsg4_report(ENOTSUP);
        return ufs_pwm_latch(ENOTSUP, false);
    }
    sweet_hsg4_diag.phase = "before-floor";
    error = ufs_hsg4_floor(0);
    if (!error) error = ufs_hsg4_sync(until, sync, true);
    if (!error) error = ufs_hsg4_stage(until, 1, 0x44);
    if (!error) error = ufs_hsg4_verify(until, 1, 0x44);
    if (!error) error = ufs_hsg4_stage(until, 4, 0x11);
    if (!error) error = ufs_hsg4_verify(until, 4, 0x11);
    if (!error) { sweet_hsg4_diag.phase = "final-capability"; error = ufs_hsg4_capabilities(until, after_max); }
    if (!error && (after_max[0] != max_gear[0] || after_max[1] != max_gear[1])) {
        unsigned int side = after_max[0] != max_gear[0] ? 0u : 1u;
        ufs_hsg4_compare(side != 0, 0x1587, "eq", max_gear[side], max_gear[side], after_max[side]);
        error = EIO;
    }
    if (!error) error = ufs_hsg4_sync(until, sync, false);
    if (!error) { sweet_hsg4_diag.phase = "final-floor"; error = ufs_hsg4_floor(0); }
    if (error) { ufs_hsg4_report(error); return ufs_pwm_failure(error); }
    printf("SWEET UFS HSG4: verified mode=0x11 gear=4/4 lanes=2/2 series=B readback=1 hardware-stability=unproven\n");
    return 0;
}
