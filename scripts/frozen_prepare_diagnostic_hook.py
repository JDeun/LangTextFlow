"""Test-build runtime hook only; never included by the production build command."""
import atexit
import faulthandler
import json
import os
import sys
import time

_path = os.environ.get("LTF_TEST_FROZEN_DIAGNOSTIC_LOG")
if _path:
    # The file must remain open through interpreter finalization and faulthandler.
    _log = open(_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    _seen = set()

    def _mark(name):
        if name not in _seen and len(_seen) < 32:
            _seen.add(name)
            _log.write(json.dumps({"marker": name, "pid": os.getpid(),
                                   "monotonic": time.monotonic()}) + "\n")
            _log.flush()

    _mark("runtime_hook_entered")
    faulthandler.enable(file=_log)
    faulthandler.dump_traceback_later(10, file=_log)

    def _dll_name(value):
        return os.path.basename(os.fsdecode(value))[:128]

    def _audit(event, args):
        if event == "ctypes.dlopen" and args and isinstance(args[0], (str, bytes)):
            _mark("dll_load_before:" + _dll_name(args[0]))

    sys.addaudithook(_audit)

    def _trace(frame, event, arg):
        filename = os.path.basename(frame.f_code.co_filename)
        function = frame.f_code.co_name
        if frame.f_globals.get("__name__") == "ctypes" and function == "__init__":
            name = frame.f_locals.get("name")
            if event == "return" and isinstance(name, (str, bytes)):
                _mark("dll_load_returned:" + _dll_name(name))
            return _trace
        if filename not in {"desktop_server.py", "m2m100_prepare_worker.py"}:
            return None
        if event == "call" and function in {"<module>", "main"}:
            _mark(filename + ":" + function + ":entered")
        elif event == "return" and function == "<module>":
            _mark(filename + ":module_import_returned")
        elif event == "line" and filename == "desktop_server.py" and function == "main":
            if frame.f_lineno == 160:
                _mark("prepare_import_before")
            elif frame.f_lineno == 162:
                _mark("prepare_import_after")
        elif event == "line" and filename == "m2m100_prepare_worker.py" and function == "main":
            if frame.f_lineno == 337:
                _mark("invalid_args_check_before")
        elif event == "exception" and isinstance(arg[1], SystemExit):
            _mark(filename + ":SystemExit:" + str(arg[1].code))
        return _trace

    sys.settrace(_trace)

    def _exit_marker():
        _mark("python_atexit")
        faulthandler.cancel_dump_traceback_later()

    atexit.register(_exit_marker)
