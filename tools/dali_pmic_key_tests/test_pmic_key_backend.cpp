#include <cassert>
#include <cstdio>
#include "DaliPmicKeyBackend.h"

struct FakePMIC {
    unsigned char top = 0, status0 = 0, status1 = 0, enable0 = 0;
    unsigned short writeAddress[4] = {};
    unsigned char writeValue[4] = {};
    unsigned writes = 0;
};

static bool fakeRead(void *opaque, unsigned short address, unsigned char *value)
{
    FakePMIC *pmic = static_cast<FakePMIC *>(opaque);
    switch (address) {
    case DALI_PMIC_TOP_IRQ_STATUS: *value = pmic->top; return true;
    case DALI_PMIC_IRQ_STATUS0: *value = pmic->status0; return true;
    case DALI_PMIC_IRQ_STATUS1: *value = pmic->status1; return true;
    case DALI_PMIC_IRQ_ENABLE0: *value = pmic->enable0; return true;
    default: return false;
    }
}

static bool fakeWrite(void *opaque, unsigned short address, unsigned char value)
{
    FakePMIC *pmic = static_cast<FakePMIC *>(opaque);
    assert(pmic->writes < 4);
    pmic->writeAddress[pmic->writes] = address;
    pmic->writeValue[pmic->writes++] = value;
    switch (address) {
    case DALI_PMIC_IRQ_STATUS0: pmic->status0 &= (unsigned char)~value; return true;
    case DALI_PMIC_IRQ_STATUS1: pmic->status1 &= (unsigned char)~value; return true;
    case DALI_PMIC_IRQ_ENABLE0: pmic->enable0 = value; return true;
    case DALI_PMIC_TOP_RCS_CLEAR: return value == 1;
    default: return false;
    }
}

int main()
{
    {
        FakePMIC pmic; pmic.enable0 = 0x80;
        assert(dali_pmic_keys_set_enabled(fakeRead, fakeWrite, &pmic, true));
        assert(pmic.enable0 == (unsigned char)(0x80 | DALI_PMIC_KEY_MASK));
        assert(pmic.writeAddress[0] == DALI_PMIC_IRQ_ENABLE0);
        assert(dali_pmic_keys_set_enabled(fakeRead, fakeWrite, &pmic, false));
        assert(pmic.enable0 == 0x80);
    }
    {
        FakePMIC pmic; pmic.top = DALI_PMIC_TOP_KEY_GROUP; pmic.status0 = 0x03;
        unsigned char pending = 0;
        assert(dali_pmic_keys_poll(fakeRead, fakeWrite, &pmic, &pending));
        assert(pending == 0x03 && pmic.status0 == 0);
        assert(pmic.writes == 2);
        assert(pmic.writeAddress[0] == DALI_PMIC_IRQ_STATUS0 && pmic.writeValue[0] == 0x03);
        assert(pmic.writeAddress[1] == DALI_PMIC_TOP_RCS_CLEAR && pmic.writeValue[1] == 1);
        DaliPmicKeyEvent events[4]; unsigned char ambiguous = 0;
        assert(dali_pmic_key_decode(pending, events, &ambiguous) == 2 && ambiguous == 0);
        assert(events[0].sweetKey == 0 && events[0].down);
        assert(events[1].sweetKey == 2 && events[1].down);
        assert(dali_pmic_key_decode(0x18, events, &ambiguous) == 2 && ambiguous == 0);
        assert(events[0].sweetKey == 0 && !events[0].down);
        assert(events[1].sweetKey == 2 && !events[1].down);
    }
    {
        /* A coalesced edge pair is marked unknown, never replayed by bit order. */
        DaliPmicKeyEvent events[4]; unsigned char ambiguous = 0;
        assert(dali_pmic_key_decode(0x09, events, &ambiguous) == 0 && ambiguous == 0x01);
        assert(dali_pmic_key_decode(0x12, events, &ambiguous) == 0 && ambiguous == 0x02);
    }
    {
        /* Preserve foreign status bits and do not clear shared RCS alongside them. */
        FakePMIC pmic; pmic.top = DALI_PMIC_TOP_KEY_GROUP; pmic.status0 = 0x81;
        unsigned char pending = 0;
        assert(dali_pmic_keys_poll(fakeRead, fakeWrite, &pmic, &pending));
        assert(pending == 0x01 && pmic.status0 == 0x80);
        assert(pmic.writes == 1 && pmic.writeAddress[0] == DALI_PMIC_IRQ_STATUS0 &&
            pmic.writeValue[0] == 0x01);
    }
    {
        FakePMIC pmic; pmic.top = DALI_PMIC_TOP_KEY_GROUP | 0x08;
        pmic.status0 = 0x01;
        unsigned char pending = 0;
        assert(dali_pmic_keys_poll(fakeRead, fakeWrite, &pmic, &pending));
        assert(pending == 0x01 && pmic.status0 == 0);
        assert(pmic.writes == 1 && pmic.writeAddress[0] == DALI_PMIC_IRQ_STATUS0);
    }
    {
        FakePMIC pmic; pmic.top = DALI_PMIC_TOP_KEY_GROUP;
        pmic.status0 = 0x01; pmic.status1 = 0x02;
        unsigned char pending = 0;
        assert(dali_pmic_keys_poll(fakeRead, fakeWrite, &pmic, &pending));
        assert(pending == 0x01 && pmic.status0 == 0);
        assert(pmic.status1 == 0x02 && pmic.writes == 1);
    }
    {
        FakePMIC pmic; unsigned char pending = 0xff;
        assert(dali_pmic_keys_poll(fakeRead, fakeWrite, &pmic, &pending));
        assert(!pending && !pmic.writes);
    }
    {
        DaliPmicKeyEvent event = {-1, false};
        assert(dali_pmic_key_event(48, &event) && event.sweetKey == 0 && event.down);
        assert(dali_pmic_key_event(49, &event) && event.sweetKey == 2 && event.down);
        assert(dali_pmic_key_event(51, &event) && event.sweetKey == 0 && !event.down);
        assert(dali_pmic_key_event(52, &event) && event.sweetKey == 2 && !event.down);
        assert(!dali_pmic_key_event(50, &event) && !dali_pmic_key_event(53, &event));
    }
    std::puts("MT6363 key status, corrected hwirq mapping, and RCS tests: passed");
}
