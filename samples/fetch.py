#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Download two real open-course sample folders for trying the coach.

  python samples/fetch.py            # both courses
  python samples/fetch.py mit-6006   # MIT OCW 6.006 (Spring 2020): 6 lecture PDFs + Quiz 1 + solutions
  python samples/fetch.py yale-psyc110  # Open Yale PSYC 110: 4 lecture transcripts as Markdown

Both are CC BY-NC-SA; they are downloaded to samples/<course>/ and are not committed.
"""
import html
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OCW = "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020"
OCW_RESOURCES = ["lec1", "lec2", "lec3", "lec4", "lec5", "lec6", "q1", "q1_sol"]
YALE = "https://oyc.yale.edu/psychology/psyc-110/lecture-%d"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "exam-cram-coach-samples/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_mit():
    out = os.path.join(HERE, "mit-6006")
    os.makedirs(out, exist_ok=True)
    for res in OCW_RESOURCES:
        page = get("%s/resources/mit6_006s20_%s/" % (OCW, res)).decode("utf-8", "ignore")
        m = re.search(r'href="(/courses/6-006[^"]*MIT6_006S20_%s\.pdf)"' % res, page, re.I)
        if not m:
            print("  ! no PDF link found for", res)
            continue
        dest = os.path.join(out, "MIT6_006S20_%s.pdf" % res)
        with open(dest, "wb") as fh:
            fh.write(get("https://ocw.mit.edu" + m.group(1)))
        print("  ", os.path.relpath(dest, HERE))
    with open(os.path.join(out, "SOURCE.md"), "w", encoding="utf-8") as fh:
        fh.write("MIT OpenCourseWare, 6.006 Introduction to Algorithms (Spring 2020), Erik Demaine, Jason Ku, Justin Solomon. "
                 "License: CC BY-NC-SA 4.0. %s\n" % OCW)


def fetch_yale():
    out = os.path.join(HERE, "yale-psyc110")
    os.makedirs(out, exist_ok=True)
    for n in range(1, 5):
        page = get(YALE % n).decode("utf-8", "ignore")
        m = re.search(r'<div id="inline_content">(.*?)<a href="#transcript-top">', page, re.S)
        if not m:
            print("  ! no transcript found for lecture", n)
            continue
        body = m.group(1)
        body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, flags=re.S)
        body = re.sub(r"<h2>", "# ", body)
        body = re.sub(r"<h3>", "\n\n## ", body)
        body = re.sub(r"</(p|h2|h3)>", "\n\n", body)
        text = html.unescape(re.sub(r"<[^>]+>", "", body))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip() + "\n"
        dest = os.path.join(out, "psyc110_lecture%02d.md" % n)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("  ", os.path.relpath(dest, HERE))
    with open(os.path.join(out, "SOURCE.md"), "w", encoding="utf-8") as fh:
        fh.write("Open Yale Courses, PSYC 110 Introduction to Psychology, Paul Bloom. "
                 "License: CC BY-NC-SA 3.0. https://oyc.yale.edu/introduction-psychology/psyc-110\n")


if __name__ == "__main__":
    which = sys.argv[1:] or ["mit-6006", "yale-psyc110"]
    for w in which:
        print("fetching", w)
        {"mit-6006": fetch_mit, "yale-psyc110": fetch_yale}[w]()
    print("done. Try: python coach.py setup samples/mit-6006 --days 3")
