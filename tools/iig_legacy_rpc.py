#!/usr/bin/env python3
"""Run pinned iig, selecting its inline-message branches for XNU 8796's IORPC."""
import collections
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

BANNER = re.compile(r'/\* iig\(DriverKit-427\.100\.5 [^\n]+\) generated from \w+\.iig \*/')
BLOCK = re.compile(r'^#ifdef KERNEL\n((?:(?!#if|#endif)[^\n]*\n)*?)#endif /\* KERNEL \*/\n', re.M)
ASSUME = ('#if __has_builtin(__builtin_assume)\n'
          '    __builtin_assume(reply != NULL);\n'
          '#endif /* __has_builtin(__builtin_assume) */\n')


def adapt(text):
    if not BANNER.fullmatch(text.splitlines()[0] if text else ''):
        raise ValueError('unsupported iig version/banner; inspect generation before adapting')
    counts = collections.Counter()

    def branch(match):
        body = match[1]
        before, separator, after = body.partition('#else /* KERNEL */\n')
        left, right = before.strip(), after.strip()
        kind = None
        if not separator:
            if re.fullmatch(r'const struct \w+_Msg_Content \* kernelContent;', left):
                kind = 'union_content_field'
            elif left == 'rpc.kernelContent = &msg->content.__hdr;':
                kind = 'content_assignment'
        elif left == '#define MESSAGE_CONTENT(__field) (messageContent->__field)' and right == '#define MESSAGE_CONTENT(__field) (message->content.__field)':
            kind = 'content_macro'
        elif left.startswith('IORPC rpc = {') and left.replace(', .kernelContent = &buf.msg.content.__hdr', '') == right and '.kernelContent' in left:
            kind = 'rpc_initializer'
        elif (left, right) in [
                ('IORPCMessage * msg = rpc.kernelContent;',
                 'IORPCMessage * msg = IORPCMessageFromMach(rpc.message, false);'),
                ('message = rpc.kernelContent;',
                 'message = IORPCMessageFromMach(rpc.reply, false);'),
                ('replyMessage = rpc.rpc.kernelContent;',
                 'replyMessage = IORPCMessageFromMach(rpc.rpc.reply, false);')]:
            kind = 'dispatch_content'
        else:
            pointer = re.fullmatch(r'const struct (\w+)_Msg \* message;', left)
            if pointer and right == f'const struct {pointer[1]}_Msg_With_Content * message;':
                kind = 'union_message_pointer'
            invoke = re.fullmatch(r'const (\w+)_Msg \* +message = rpc.message;\n\s*const \1_Msg_Content \* messageContent = rpc.kernelContent;', left)
            if invoke and right == f'const {invoke[1]}_Msg_With_Content * message = rpc.message;':
                kind = 'invoke_content'
        if kind is None:
            return match[0]
        counts[kind] += 1
        return after if separator else ''

    result = BLOCK.sub(branch, text)
    counts['reply_assumptions'] = result.count(ASSUME)
    result = result.replace(ASSUME, '')
    if 'kernelContent' in result or '__builtin_assume(' in result:
        raise ValueError('unrecognized iig content/assumption pattern; refusing partial adaptation')
    if counts['union_content_field'] != counts['union_message_pointer'] or counts['union_content_field'] != counts['invoke_content']:
        raise ValueError('inconsistent invocation layout; refusing adaptation')
    return result, dict(counts)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        raise ValueError('usage: iig_legacy_rpc.py REAL_IIG IORPC_HEADER [iig arguments including --impl PATH]')
    real, header, arguments = argv[0], Path(argv[1]), argv[2:]
    layout = re.search(r'struct IORPC \{([^}]+)\};', header.read_text())
    expected = 'IORPCMessageMach*message;IORPCMessageMach*reply;uint32_tsendSize;uint32_treplySize;'
    if layout is None or re.sub(r'\s+', '', layout[1]) != expected:
        raise ValueError('expected exact legacy four-field IORPC layout; no generation attempted')
    if arguments.count('--impl') != 1:
        raise ValueError('one explicit --impl output is required')
    position = arguments.index('--impl') + 1
    if position >= len(arguments) or arguments[position].startswith('--'):
        raise ValueError('missing --impl path')
    output = Path(arguments[position])
    subprocess.run([real, *arguments], check=True)
    original = output.read_text()
    adapted, counts = adapt(original)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', prefix=output.name+'.legacy-',
                                         dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), output.stat().st_mode & 0o777)
            stream.write(adapted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print('SWEET IIG LEGACY: ' + output.name + ' ' + repr(counts), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('iig legacy adapter failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
