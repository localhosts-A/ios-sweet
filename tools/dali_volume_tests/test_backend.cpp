#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>
#include "DaliVolumeKey.h"

/* Host mocks call the extracted, otherwise unchanged production backend.
 * The extraction replaces only the AArch64 dmb instruction with a no-op. */
using UInt32 = unsigned int;
using IOReturn = int;
constexpr int kIOReturnSuccess = 0, kIOReturnBadArgument = -1;
constexpr int kIODirectionIn = 1, kIOMapInhibitCache = 2, kIOMapReadOnly = 4;
constexpr UInt32 kHIDUsage_Csmr_VolumeIncrement = 0xe9;
struct OSObject { virtual ~OSObject() = default; };
#define OSDynamicCast(T, P) dynamic_cast<T *>(P)
#define OSSafeReleaseNULL(P) do { if (P) { (P)->release(); (P) = nullptr; } } while (0)
static void test_log(const char *, ...) {}
#define IOLog(...) test_log(__VA_ARGS__)
static int optIn = 0, descCount = 0, mapCount = 0, timerCount = 0, mapCalls = 0;
static int failMap = 0; static bool failTimer = false, failAdd = false;
static uint64_t fakeNs = 0;
alignas(4) static unsigned char gpio[0x1000], ies[0x1000];
static void put32(unsigned char *p, unsigned n, uint32_t v) { std::memcpy(p+n,&v,4); }
uint64_t mach_absolute_time() { return fakeNs; }
void absolutetime_to_nanoseconds(uint64_t ticks, uint64_t *ns) { *ns = ticks; }
bool PE_parse_boot_argn(const char *name, void *out, int size) {
    assert(std::strcmp(name,"dalivolup")==0 && size == int(sizeof(int)));
    *static_cast<int *>(out) = optIn; return optIn != 0;
}
struct OSData : OSObject {
    const char *data; unsigned size;
    OSData(const char *p, unsigned n):data(p),size(n) {}
    const void *getBytesNoCopy() { return data; }
    unsigned getLength() { return size; }
};
struct IORegistryEntry {
    static OSData *compat;
    static std::shared_ptr<IORegistryEntry> fromPath(const char *p) {
        assert(std::strcmp(p,"IODeviceTree:/")==0);
        return std::make_shared<IORegistryEntry>();
    }
    OSObject *getProperty(const char *p) { assert(std::strcmp(p,"compatible")==0); return compat; }
};
OSData *IORegistryEntry::compat = nullptr;
struct IOMemoryMap {
    unsigned char *io;
    explicit IOMemoryMap(unsigned char *p):io(p) { ++mapCount; }
    uintptr_t getVirtualAddress() { return reinterpret_cast<uintptr_t>(io); }
    void release() { --mapCount; delete this; }
};
struct IOMemoryDescriptor {
    unsigned char *io;
    explicit IOMemoryDescriptor(unsigned char *p):io(p) { ++descCount; }
    static IOMemoryDescriptor *withPhysicalAddress(uint64_t p, int len, int dir) {
        assert(len == 0x1000 && dir == kIODirectionIn);
        if (p == 0x1002d000ULL) return new IOMemoryDescriptor(gpio);
        if (p == 0x13020000ULL) return new IOMemoryDescriptor(ies);
        assert(false); return nullptr;
    }
    IOMemoryMap *map(int flags) {
        assert(flags == (kIOMapInhibitCache|kIOMapReadOnly));
        ++mapCalls; if (mapCalls == failMap) return nullptr;
        return new IOMemoryMap(io);
    }
    void release() { --descCount; delete this; }
};
struct IOTimerEventSource {
    using Action = void (*)(OSObject *, IOTimerEventSource *);
    OSObject *owner; Action action; bool armed = false, enabled = true;
    IOTimerEventSource(OSObject *o, Action a):owner(o),action(a) { ++timerCount; }
    static IOTimerEventSource *timerEventSource(OSObject *o, Action a) {
        return failTimer ? nullptr : new IOTimerEventSource(o,a);
    }
    void setTimeoutMS(int n) { assert(n==16); armed = true; }
    void cancelTimeout() { armed = false; }
    void disable() { enabled = false; }
    void release() { --timerCount; delete this; }
    void fire() { assert(armed && enabled); armed=false; action(owner,this); }
};
struct WorkLoop {
    int addEventSource(IOTimerEventSource *) { return failAdd ? -1 : 0; }
    void removeEventSource(IOTimerEventSource *) {}
    IOReturn runAction(IOReturn(*f)(OSObject *,void *,void *,void *,void *), OSObject *o) {
        return f(o,nullptr,nullptr,nullptr,nullptr);
    }
};
struct ClientList { int count = 0; unsigned getCount() { return unsigned(count); } };
void IOLockLock(void *) {}
void IOLockUnlock(void *) {}
struct SweetHIDButtons : OSObject {
    WorkLoop * _hidWorkLoop;
    void *_clientLock = nullptr;
    ClientList *_clients;
    IOMemoryDescriptor *fDaliGpioMemory = nullptr, *fDaliIesMemory = nullptr;
    IOMemoryMap *fDaliGpioMap = nullptr, *fDaliIesMap = nullptr;
    IOTimerEventSource *fDaliVolumeTimer = nullptr;
    bool fDaliVolumeTimerAdded = false, fDaliVolumeRunning = false;
    DaliVolumeKeyState fDaliVolumeState = {};
    std::vector<int> events;
    bool failKey = false;
    SweetHIDButtons(WorkLoop *w, ClientList *c):_hidWorkLoop(w),_clients(c) {}
    bool key(UInt32 usage, bool down) {
        assert(usage==kHIDUsage_Csmr_VolumeIncrement);
        if (failKey) return false;
        events.push_back(down?1:0); return true;
    }
    void startDaliVolume(); void stopDaliVolume();
    bool daliVolumeHasClient(); bool daliVolumePinValid();
    static UInt32 daliVolumeRead(IOMemoryMap *, UInt32);
    static void daliVolumeFired(OSObject *, IOTimerEventSource *);
    static IOReturn daliVolumeStopAction(OSObject *, void *, void *, void *, void *);
};
#include "actual_backend.inc"

int main() {
    const char good[] = "D22AP\0xiaomi,dali\0mediatek,mt6991\0";
    const char bad[] = "xiaomi,sweet\0";
    OSData goodData(good,sizeof(good)), badData(bad,sizeof(bad));
    WorkLoop loop; ClientList clients;
    IORegistryEntry::compat = &goodData;
    std::memset(gpio, 0, sizeof(gpio)); std::memset(ies, 0, sizeof(ies));
    put32(ies, 0x70, 1U << 19); put32(gpio, 0x210, 1U << 28);
    { SweetHIDButtons s(&loop,&clients); optIn=0; s.startDaliVolume(); assert(!mapCalls && !descCount); s.stopDaliVolume(); }
    { SweetHIDButtons s(&loop,&clients); optIn=1; IORegistryEntry::compat=&badData; s.startDaliVolume(); assert(!mapCalls && !descCount); s.stopDaliVolume(); }
    IORegistryEntry::compat=&goodData;
    { SweetHIDButtons s(&loop,&clients); failMap=2; mapCalls=0; s.startDaliVolume(); assert(descCount==0 && mapCount==0 && timerCount==0); s.stopDaliVolume(); }
    failMap=0;
    { SweetHIDButtons s(&loop,&clients); mapCalls=0; put32(gpio,0x370,1U<<16); s.startDaliVolume(); assert(descCount==0 && mapCount==0 && timerCount==0); put32(gpio,0x370,0); }
    { SweetHIDButtons s(&loop,&clients); mapCalls=0; put32(gpio,0x10,1U<<28); s.startDaliVolume(); assert(descCount==0 && mapCount==0 && timerCount==0); put32(gpio,0x10,0); }
    { SweetHIDButtons s(&loop,&clients); mapCalls=0; put32(ies,0x70,0); s.startDaliVolume(); assert(descCount==0 && mapCount==0 && timerCount==0); put32(ies,0x70,1U<<19); }
    { SweetHIDButtons s(&loop,&clients); mapCalls=0; failTimer=true; s.startDaliVolume(); assert(descCount==0 && mapCount==0 && timerCount==0); failTimer=false; }
    { SweetHIDButtons s(&loop,&clients); mapCalls=0; failAdd=true; s.startDaliVolume(); assert(descCount==0 && mapCount==0 && timerCount==0); failAdd=false; }
    {
        SweetHIDButtons s(&loop,&clients); mapCalls=0; s.startDaliVolume();
        assert(descCount==2 && mapCount==2 && timerCount==1 && s.fDaliVolumeRunning);
        auto tick=[&](uint64_t ms){
            unsigned char beforeGpio[sizeof(gpio)], beforeIes[sizeof(ies)];
            std::memcpy(beforeGpio,gpio,sizeof(gpio)); std::memcpy(beforeIes,ies,sizeof(ies));
            fakeNs=ms*1000000; s.fDaliVolumeTimer->fire();
            assert(std::memcmp(beforeGpio,gpio,sizeof(gpio))==0);
            assert(std::memcmp(beforeIes,ies,sizeof(ies))==0);
        };
        /* No client; even a held pin produces no delivered event. */
        put32(gpio,0x210,0); tick(0); tick(40); assert(s.events.empty());
        clients.count=1; tick(50); tick(90); assert(s.events.empty() && !s.fDaliVolumeState.armed);
        put32(gpio,0x210,1U<<28); tick(100); tick(132); assert(s.fDaliVolumeState.armed);
        put32(gpio,0x210,0); tick(140); tick(171); assert(s.events.empty());
        s.failKey=true; tick(172); assert(s.events.empty() && !s.fDaliVolumeState.down);
        s.failKey=false; tick(173); assert((s.events==std::vector<int>{1}));
        put32(gpio,0x210,1U<<28); tick(180); s.failKey=true; tick(212);
        assert((s.events==std::vector<int>{1}) && s.fDaliVolumeState.down);
        s.failKey=false; tick(213); assert((s.events==std::vector<int>{1,0}));
        put32(gpio,0x210,0); tick(220); tick(252); assert((s.events==std::vector<int>{1,0,1}));
        /* Invalid config after a down sends one cancellation release and stops. */
        put32(ies,0x70,0); tick(260); assert((s.events==std::vector<int>{1,0,1,0}));
        assert(!s.fDaliVolumeRunning && !s.fDaliVolumeTimer->armed);
        s.stopDaliVolume(); s.stopDaliVolume();
        assert(descCount==0 && mapCount==0 && timerCount==0);
        assert((s.events==std::vector<int>{1,0,1,0}));
    }
    /* Service stop balances an outstanding down exactly once. */
    {
        put32(ies,0x70,1U<<19); put32(gpio,0x210,1U<<28);
        SweetHIDButtons s(&loop,&clients); mapCalls=0; s.startDaliVolume();
        auto tick=[&](uint64_t ms){ fakeNs=ms*1000000; s.fDaliVolumeTimer->fire(); };
        tick(300); tick(332); put32(gpio,0x210,0); tick(340); tick(372);
        assert((s.events==std::vector<int>{1})); s.stopDaliVolume(); s.stopDaliVolume();
        assert((s.events==std::vector<int>{1,0}));
        assert(descCount==0 && mapCount==0 && timerCount==0);
    }
    std::puts("actual backend with host MMIO/HID mocks: all cases passed");
}
