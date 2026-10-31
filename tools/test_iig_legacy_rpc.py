#!/usr/bin/env python3
"""Exercise legacy inline IORPC adaptation, layouts and fail-closed generation."""
from pathlib import Path
import os
import shutil
import shlex
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import iig_legacy_rpc as adapter

HEADER = '''struct IORPC {
    IORPCMessageMach * message;
    IORPCMessageMach * reply;
    uint32_t sendSize;
    uint32_t replySize;
};'''
FIXTURE = '''/* iig(DriverKit-427.100.5 fixture) generated from Fixture.iig */
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
struct IORPCMessageMach { uint32_t descriptors, reserved; };
struct IORPCMessage { uint64_t msgid, flags, objectRefs; };
''' + HEADER + '''
#pragma pack(push,4)
struct Fixture_Call_Msg_Content { IORPCMessage __hdr; uint64_t value; };
struct Fixture_Call_Msg { IORPCMessageMach mach; uint64_t descriptor[2]; };
struct Fixture_Call_Msg_With_Content { IORPCMessageMach mach; uint64_t descriptor[2]; Fixture_Call_Msg_Content content; };
struct Reply_With_Content { IORPCMessageMach mach; IORPCMessage content; uint64_t result; };
#pragma pack(pop)
typedef union {
    const IORPC rpc;
    struct {
#ifdef KERNEL
        const struct Fixture_Call_Msg * message;
#else /* KERNEL */
        const struct Fixture_Call_Msg_With_Content * message;
#endif /* KERNEL */
        struct Reply_With_Content * reply;
        uint32_t sendSize;
        uint32_t replySize;
#ifdef KERNEL
        const struct Fixture_Call_Msg_Content * kernelContent;
#endif /* KERNEL */
    };
} Invocation;
static IORPCMessage * IORPCMessageFromMach(IORPCMessageMach *message,bool is_reply) {
    assert(!is_reply);
    return reinterpret_cast<IORPCMessage *>(reinterpret_cast<char *>(message)+sizeof(*message)+message->descriptors*sizeof(uint64_t));
}
#ifdef KERNEL
#define MESSAGE_CONTENT(__field) (messageContent->__field)
#else /* KERNEL */
#define MESSAGE_CONTENT(__field) (message->content.__field)
#endif /* KERNEL */
static uint64_t dispatched(IORPC rpc) {
#ifdef KERNEL
    IORPCMessage * msg = rpc.kernelContent;
#else /* KERNEL */
    IORPCMessage * msg = IORPCMessageFromMach(rpc.message, false);
#endif /* KERNEL */
    return msg->msgid;
}
static uint64_t from_reply(IORPC rpc) {
    IORPCMessage *message;
#ifdef KERNEL
    message = rpc.kernelContent;
#else /* KERNEL */
    message = IORPCMessageFromMach(rpc.reply, false);
#endif /* KERNEL */
    return message->msgid;
}
static uint64_t from_union(Invocation rpc) {
    IORPCMessage *replyMessage;
#ifdef KERNEL
    replyMessage = rpc.rpc.kernelContent;
#else /* KERNEL */
    replyMessage = IORPCMessageFromMach(rpc.rpc.reply, false);
#endif /* KERNEL */
    return replyMessage->msgid;
}
static IORPC assigned(IORPC rpc,Fixture_Call_Msg_With_Content *msg) {
#ifdef KERNEL
    rpc.kernelContent = &msg->content.__hdr;
#endif /* KERNEL */
    rpc.message=&msg->mach;return rpc;
}
static int invoked(const IORPC input,int callback_result) {
    Invocation rpc={input};
    Reply_With_Content *reply=rpc.reply;
#ifdef KERNEL
    const Fixture_Call_Msg *         message = rpc.message;
    const Fixture_Call_Msg_Content * messageContent = rpc.kernelContent;
#else /* KERNEL */
    const Fixture_Call_Msg_With_Content * message = rpc.message;
#endif /* KERNEL */
#if __has_builtin(__builtin_assume)
    __builtin_assume(reply != NULL);
#endif /* __has_builtin(__builtin_assume) */
    if(message->mach.descriptors!=2)return 11;
    if(MESSAGE_CONTENT(__hdr.objectRefs)!=2)return 12;
    if(rpc.sendSize<sizeof(Fixture_Call_Msg_With_Content))return 13;
    if(reply && rpc.replySize<sizeof(*reply))return 14;
    if(callback_result)return callback_result;
    reply->result=MESSAGE_CONTENT(value);return 0;
}
static IORPC created(Fixture_Call_Msg_With_Content &request,Reply_With_Content &reply) {
    struct { Fixture_Call_Msg_With_Content msg; struct { Reply_With_Content rpl; } rpl; } buf={request,{reply}};
#ifdef KERNEL
    IORPC rpc = { .message = &buf.msg.mach, .reply = &buf.rpl.rpl.mach, .sendSize = sizeof(buf.msg), .replySize = sizeof(buf.rpl), .kernelContent = &buf.msg.content.__hdr };
#else /* KERNEL */
    IORPC rpc = { .message = &buf.msg.mach, .reply = &buf.rpl.rpl.mach, .sendSize = sizeof(buf.msg), .replySize = sizeof(buf.rpl) };
#endif /* KERNEL */
    assert(rpc.sendSize==sizeof(request) && rpc.replySize==sizeof(reply));
    rpc.message=&request.mach;rpc.reply=&reply.mach;return rpc;
}
int main() {
    static_assert(sizeof(IORPC)==24 && sizeof(Invocation)==24);
    static_assert(offsetof(Invocation,sendSize)==16 && offsetof(Invocation,replySize)==20);
    static_assert(offsetof(Fixture_Call_Msg_With_Content,content)==sizeof(Fixture_Call_Msg));
    Fixture_Call_Msg_With_Content request={};request.mach.descriptors=2;
    request.content.__hdr={41,0,2};request.content.value=99;
    Reply_With_Content reply={};reply.content.msgid=73;
    IORPC rpc=created(request,reply);rpc=assigned(rpc,&request);
    assert(dispatched(rpc)==41 && from_reply(rpc)==73 && from_union(Invocation{rpc})==73);
    assert(invoked(rpc,0)==0 && reply.result==99);
    request.mach.descriptors=1;assert(invoked(rpc,0)==11);request.mach.descriptors=2;
    request.content.__hdr.objectRefs=3;assert(invoked(rpc,0)==12);request.content.__hdr.objectRefs=2;
    --rpc.sendSize;assert(invoked(rpc,0)==13);++rpc.sendSize;
    --rpc.replySize;assert(invoked(rpc,0)==14);++rpc.replySize;
    assert(invoked(rpc,19)==19);rpc.reply=nullptr;assert(invoked(rpc,19)==19);
    return 0;
}
'''


class IIGLegacyTest(unittest.TestCase):
    def test_compiled_inline_message_layout_dispatch_and_errors(self):
        source, counts = adapter.adapt(FIXTURE)
        self.assertEqual(counts['dispatch_content'], 3)
        self.assertEqual(counts['union_content_field'], 1)
        self.assertEqual(counts['invoke_content'], 1)
        self.assertEqual(counts['reply_assumptions'], 1)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'test.cpp').write_text(source)
            subprocess.run([shutil.which('clang++') or shutil.which('c++'), '-std=c++20',
                            '-O2', '-Wall', '-Wextra', '-Werror', '-DKERNEL=1',
                            str(root/'test.cpp'), '-o', str(root/'test')], check=True)
            subprocess.run([str(root/'test')], check=True, timeout=10)

    def test_unadapted_new_generator_rejects_legacy_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'raw.cpp').write_text(FIXTURE)
            result = subprocess.run([shutil.which('clang++') or shutil.which('c++'),
                                     '-std=c++20', '-O2', '-Wall', '-Wextra', '-Werror',
                                     '-DKERNEL=1', str(root/'raw.cpp'), '-o', str(root/'raw')],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('kernelContent', result.stderr)

    def test_make_preserves_exported_iig_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);tools = root/'tool chain';tools.mkdir()
            real, header, source = self.setup_generator(tools)
            (root/'Makefile').write_text('ifeq ($(origin IIG),undefined)\n'
                                        'export IIG := false\nendif\n'
                                        'all:\n\t$(IIG) --def Fixture.iig --impl out.cpp -- -DKERNEL=1\n')
            environment = dict(os.environ, SOURCE=str(source), ARG_LOG=str(root/'args'),
                               IIG=shlex.join([sys.executable, '-I', str(Path(adapter.__file__).resolve()),
                                              str(real), str(header)]))
            subprocess.run(['make', '-f', 'Makefile'], cwd=root, env=environment,
                           capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual((root/'out.cpp').read_text(), adapter.adapt(FIXTURE)[0])

    def test_unknown_version_or_pattern_is_rejected(self):
        for source in ['', FIXTURE.replace('427.100.5', '428.0'),
                       FIXTURE + '\nvoid unknown() { rpc.kernelContent = other; }\n',
                       FIXTURE.replace('reply != NULL', 'other != NULL')]:
            with self.subTest(source=source[:70]), self.assertRaises(ValueError):
                adapter.adapt(source)

    def test_inconsistent_invocation_layout_is_rejected(self):
        source = FIXTURE.replace('        const struct Fixture_Call_Msg_Content * kernelContent;',
                                 '        const struct Fixture_Call_Msg_Content * removed;')
        with self.assertRaises(ValueError):
            adapter.adapt(source)

    def setup_generator(self, root):
        real = root/'fake-iig'
        real.write_text('''#!/bin/sh
printf '%s\\n' "$@" > "$ARG_LOG"
if [ "${FAIL_GEN:-0}" != 0 ]; then exit 7; fi
while [ "$#" -gt 0 ]; do
  if [ "$1" = --impl ]; then out="$2"; shift 2; else shift; fi
done
cp "$SOURCE" "$out"
''')
        real.chmod(0o700)
        header = root/'IORPC.h';header.write_text(HEADER)
        source = root/'input.cpp';source.write_text(FIXTURE)
        return real, header, source

    def test_generator_arguments_and_atomic_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);real, header, source = self.setup_generator(root)
            output = root/'output file.cpp';arguments = ['--def', 'Fixture.iig', '--impl',
                                                       str(output), '--', '-DVALUE=has spaces']
            with patch.dict(os.environ, SOURCE=str(source), ARG_LOG=str(root/'args')):
                self.assertEqual(adapter.main([str(real), str(header), *arguments]), 0)
            self.assertEqual((root/'args').read_text().splitlines(), arguments)
            self.assertEqual(output.read_text(), adapter.adapt(FIXTURE)[0])
            self.assertFalse(list(root.glob('*.legacy-*')))
            self.assertEqual(source.read_text(), FIXTURE)

    def test_wrong_iorpc_layout_rejected_before_generator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);real, header, source = self.setup_generator(root)
            header.write_text(HEADER.replace('uint32_t sendSize;', 'void *kernelContent; uint32_t sendSize;'))
            with patch.dict(os.environ, SOURCE=str(source), ARG_LOG=str(root/'args')):
                with self.assertRaises(ValueError):
                    adapter.main([str(real), str(header), '--impl', str(root/'out.cpp')])
            self.assertFalse((root/'args').exists())
            self.assertFalse((root/'out.cpp').exists())

    def test_generator_failure_or_bad_arguments_do_not_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);real, header, source = self.setup_generator(root)
            output = root/'out.cpp';output.write_text('existing')
            with patch.dict(os.environ, SOURCE=str(source), ARG_LOG=str(root/'args'), FAIL_GEN='1'):
                with self.assertRaises(subprocess.CalledProcessError):
                    adapter.main([str(real), str(header), '--impl', str(output)])
            self.assertEqual(output.read_text(), 'existing')
            for arguments in [[], [str(real), str(header)],
                              [str(real), str(header), '--impl'],
                              [str(real), str(header), '--impl', 'a', '--impl', 'b']]:
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    adapter.main(arguments)


if __name__ == '__main__':
    unittest.main()
