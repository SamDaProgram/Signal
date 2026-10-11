#!/bin/zsh
cd "${0:A:h}"
if ! command -v python3 >/dev/null 2>&1; then
  print 'Python 3 is required. Install Python 3 from https://www.python.org/downloads/macos/'
  read '?Press Return to close.'
  exit 1
fi
signal_python="$(command -v python3)"
if [[ -x "$PWD/.venv/bin/python3" ]]; then
  signal_python="$PWD/.venv/bin/python3"
fi
"$signal_python" app.py "$@"
result=$?
if [[ $result -eq 77 ]]; then
  print 'Port 80 requires administrator permission on this Mac.'
  print 'Enter your Mac password to open the ports. The server then runs as your normal account.'
  sudo "$signal_python" "$PWD/app.py" "$@"
  result=$?
fi
if [[ $result -ne 0 ]]; then
  read '?Press Return to close.'
fi
