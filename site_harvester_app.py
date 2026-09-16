#!/usr/bin/env python3
"""Entry point for the OPTIONAL standalone build of Site Harvester (see
build-exe.bat / build-app.command). The normal way to run Site Harvester is
run.bat / run.command - this file changes nothing about that.

    Site Harvester             opens the app
    Site Harvester selftest    prints what the build can and cannot do, and exits

A windowed build has nowhere to print to: if it fails while starting up, the
window never appears and macOS or Windows simply closes it with no message. So
anything that goes wrong here is written to harvester-crash.log beside the app
and, where Tk still works, shown in a dialog.
"""

import os
import sys
import traceback
from multiprocessing import freeze_support


def _app_dir():
    """The folder the app treats as its own - settings, ./tools, crash reports.

    Same rule as site_harvester._app_dir(). It is repeated here because this
    file has to keep working when importing the app is the thing that failed.

    Normally that folder is the one holding the scripts. In a standalone build
    it is the folder holding the exe, and on macOS the folder *containing*
    Site Harvester.app - the executable sits three levels down inside the
    bundle, and anything written inside a bundle invalidates its code signature
    (the next launch is then killed by Gatekeeper).
    """
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.realpath(__file__))
    exe_dir = os.path.dirname(os.path.realpath(sys.executable))
    parts = exe_dir.split(os.sep)
    if (sys.platform == "darwin" and len(parts) >= 3
            and parts[-1] == "MacOS" and parts[-2] == "Contents"
            and parts[-3].endswith(".app")):
        return os.path.dirname(os.path.dirname(os.path.dirname(exe_dir)))
    return exe_dir


def _crash_log_path():
    """Somewhere writable to record a start-up failure."""
    folder = _app_dir()
    if os.access(folder, os.W_OK):
        return os.path.join(folder, "harvester-crash.log")
    import tempfile
    return os.path.join(tempfile.gettempdir(), "harvester-crash.log")


def _report(exc):
    """Record a start-up failure and, if possible, show it."""
    text = "".join(traceback.format_exception(
        type(exc), exc, exc.__traceback__))
    path = _crash_log_path()
    try:
        import datetime
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n===== {} =====\npython {}\nexecutable {}\n{}\n".format(
                datetime.datetime.now().isoformat(timespec="seconds"),
                sys.version.replace("\n", " "), sys.executable, text))
    except Exception:
        path = "(could not be written)"
    sys.stderr.write(text)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Site Harvester could not start",
            "{}\n\nFull details: {}".format(text.strip().splitlines()[-1], path))
        root.destroy()
    except Exception:
        pass


def selftest():
    """Report what this build actually has. Run by the build scripts.

    A windowed build has no console, so the report is also written to
    harvester-selftest.txt beside the app - that file is the thing to send on
    when a build misbehaves.
    """
    lines = []

    def print(*args):                      # noqa: A001 - capture as well as show
        text = " ".join(str(a) for a in args)
        lines.append(text)
        sys.__stdout__ and sys.__stdout__.write(text + "\n")

    folder = _app_dir()
    print("executable : {}".format(sys.executable))
    print("python     : {}".format(sys.version.replace("\n", " ")))
    print("frozen     : {}".format(bool(getattr(sys, "frozen", False))))
    print("data folder: {}".format(folder))
    print("            writable: {}".format(os.access(folder, os.W_OK)))
    ok = os.access(folder, os.W_OK)

    try:
        import tkinter
        root = tkinter.Tk()
        print("tk         : {} (patch {})".format(
            tkinter.TkVersion, root.tk.call("info", "patchlevel")))
        root.destroy()
    except Exception as exc:
        ok = False
        print("tk         : FAILED - {}".format(exc))

    # The crawler cannot work without these two, so a miss here is a failure.
    for label, mod in (("requests", "requests"),
                       ("beautifulsoup4", "bs4")):
        try:
            __import__(mod)
            print("  {:<24} yes".format(label))
        except Exception as exc:
            ok = False
            print("  {:<24} MISSING - {}".format(label, exc))

    # These only cost you a feature each, so a miss is worth saying but not
    # worth failing the build over.
    for label, mod in (("playwright (PDF, JS)", "playwright"),
                       ("pypdf (merges the PDF)", "pypdf"),
                       ("yt-dlp (embedded video)", "yt_dlp"),
                       ("weasyprint (fallback)", "weasyprint")):
        try:
            __import__(mod)
            print("  {:<24} yes".format(label))
        except Exception:
            print("  {:<24} no".format(label))

    try:
        import site_harvester
        print("app        : imports cleanly")

        # Playwright's browser is downloaded to a shared cache, not bundled;
        # importing site_harvester is what points the app at it.
        cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if cache and os.path.isdir(cache):
            browsers = [d for d in os.listdir(cache)
                        if d.startswith("chromium")]
            print("  {:<24} {}".format(
                "headless Chromium",
                "yes ({})".format(cache) if browsers
                else "NOT DOWNLOADED - run: playwright install chromium"))
        else:
            print("  {:<24} not found - run: playwright install chromium"
                  .format("headless Chromium"))

        ffdir = site_harvester.find_ffmpeg_dir()
        print("  {:<24} {}".format(
            "ffmpeg (best quality)", ffdir if ffdir else "no"))
    except Exception as exc:
        ok = False
        print("app        : FAILED - {}".format(exc))

    print("RESULT: {}".format("ok" if ok else "PROBLEMS FOUND"))

    report = os.path.join(folder, "harvester-selftest.txt")
    try:
        with open(report, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return 0 if ok else 1


def main():
    freeze_support()    # lets worker processes start inside a frozen build

    # macOS gives a Finder-launched .app an extra "-psn_0_12345" argument.
    # Anything that reads sys.argv has to drop it or the app exits instantly
    # with no window and nothing said.
    argv = [a for a in sys.argv[1:] if not a.startswith("-psn_")]

    if argv and argv[0] == "selftest":
        return selftest()

    try:
        import site_harvester
        return site_harvester.main(argv)
    except SystemExit:
        raise
    except BaseException as exc:      # noqa: BLE001 - last chance to say why
        _report(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
