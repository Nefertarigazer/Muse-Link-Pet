#!/usr/bin/env bash
# pc.sh - the standard SSH / scp entry point from the Muse VM to the PC
# Legacy command wrappers in vm/compat/ forward here.
#   pc.sh ssh "<remote command>"          run a command (remote shell: cmd.exe)
#   pc.sh ps  "<PowerShell code>"         run PowerShell via -EncodedCommand
#   pc.sh scp-to   <local> <remote path>  remote path with FORWARD slashes
#   pc.sh scp-from <remote path> <local>
# All options (key, known_hosts, keepalive, tunnel proxy, connection reuse)
# come from lib/muselink-common.sh. ServerAlive*: a silently dropped proxied
# connection otherwise leaves ssh blocked on a half-open socket.
set -u
. "$(dirname "$0")/lib/muselink-common.sh" || exit 2
case "${1:-}" in
  ssh)      shift; ml_ssh "$@" ;;
  ps)       shift; ml_ps "${1:-}" ;;
  scp-to)   ml_scp_to "$2" "$3" ;;
  scp-from) ml_scp_from "$2" "$3" ;;
  *) echo "usage: pc.sh ssh|ps|scp-to|scp-from ..." >&2; exit 2 ;;
esac
