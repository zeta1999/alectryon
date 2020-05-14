# Copyright © 2019 Clément Pit-Claudel
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import json
import os.path
import re
import sys

from dominate import tags, document

from . import core
from .core import CoqSentence, CoqGoal, CoqHypothesis, CoqText, annotate, GENERATOR, __version__
from .html import HtmlWriter
from .pygments import highlight, FORMATTER

ARGDOC = ".\n".join([
    core.__doc__, "When run as a standalone application, take input as multiple "
    ".v or .json files, and create one .io.json file per input file."
])

COQ_SPLIT_RE = re.compile(r"(?:[ \t]*\n){2,}")

def partition_fragments(fragments):
    """Split a list of `fragments` into whitespace-delimited runs.

    This function makes it easy to further subdivide the results of processing a
    chunk.  This is useful when processing large chunks, or when chunk
    boundaries are not known beforehand (e.g. when processing a Coq file).

    Separators are added to the following run of fragments, or discarded if
    `discard` is truthy.
    """
    partitioned = [[]]
    for fragment in fragments:
        if isinstance(fragment, CoqText):
            m = COQ_SPLIT_RE.match(fragment.string)
            if m:
                if partitioned[-1]:
                    partitioned.append([])
                fragment = fragment._replace(string=fragment.string[m.end():])
                if not fragment.string:
                    continue
        partitioned[-1].append(fragment)
    return partitioned

def split_single_chunk(chunks):
    """Split a singleton list of `chunks` into multiple chunks.

    When parsing Coq files we don't know where relevant boundaries are
    beforehand, so we process everything as one chunk and split afterwards.
    """
    assert len(chunks) == 1
    return partition_fragments(chunks[0])

def read_input(fpath):
    _fdir, fname = os.path.split(fpath)
    _fn, fext = os.path.splitext(fname)
    with open(fpath) as src:
        if fext == '.v':
            return fname, [src.read()], split_single_chunk
        if fext == '.json':
            return fname, json.load(src), (lambda chunks: chunks)
        MSG = "Input files must have extension .v or .json ({})."
        raise argparse.ArgumentTypeError(MSG.format(fname))

def parse_arguments():
    parser = argparse.ArgumentParser(description=ARGDOC)

    INPUT_HELP = """Input file.  Can be either .v (plain Coq code) or \
.json (a list of Coq fragments)."""
    parser.add_argument("input", nargs="+",
                        type=read_input, help=INPUT_HELP)

    DEBUG_HELP = "Print communications with SerAPI."
    parser.add_argument("--debug", action="store_true",
                        default=False, help=DEBUG_HELP)


    OUTPUT_HELP = "Configure the output"
    out = parser.add_argument_group("Output arguments", OUTPUT_HELP)

    WRITER_HELP = """Output type"""
    WRITER_CHOICES = ("json", "html", "webpage")
    out.add_argument("--writer", default="webpage",
                     choices=WRITER_CHOICES, help=WRITER_HELP)

    OUT_DIR_HELP = "Set the output directory."
    parser.add_argument("--output-directory", default=".",
                        help=OUT_DIR_HELP)


    SUBP_HELP = "Pass arguments to the SerAPI process"
    subp = parser.add_argument_group("Subprocess arguments", SUBP_HELP)

    SERAPI_ARGS_HELP = "Pass a single argument to SerAPI (e.g. -Q dir,lib)."
    subp.add_argument("--serapi-arg", dest="serapi_args",
                      action="append", default=[],
                      help=SERAPI_ARGS_HELP)

    I_HELP="Pass -I DIR to the SerAPI subprocess."
    subp.add_argument("-I", "--ml-include-path", dest="coq_args_I",
                      metavar="dir", nargs=1, action="append",
                      default=[], help=I_HELP)

    Q_HELP="Pass -Q DIR COQDIR to the SerAPI subprocess."
    subp.add_argument("-Q", "--load-path", dest="coq_args_Q",
                      metavar="DIR COQDIR", nargs=2, action="append",
                      default=[], help=Q_HELP)

    R_HELP="Pass -R DIR COQDIR to the SerAPI subprocess."
    subp.add_argument("-R", "--rec-load-path", dest="coq_args_R",
                      metavar="DIR COQDIR", nargs=2, action="append",
                      default=[], help=R_HELP)


    args = parser.parse_args()
    for dir in args.coq_args_I:
        args.serapi_args.extend(("-I", dir))
    for pair in args.coq_args_R:
        args.serapi_args.extend(("-R", ",".join(pair)))
    for pair in args.coq_args_Q:
        args.serapi_args.extend(("-Q", ",".join(pair)))

    return args

COQ_TYPES = (CoqSentence, CoqGoal, CoqHypothesis, CoqText)
COQ_TYPE_NAMES = {
    "CoqHypothesis": "hypothesis",
    "CoqGoal": "goal",
    "CoqSentence": "sentence",
    "HTMLSentence": "html_sentence",
    "CoqText": "text",
}

def prepare_json(obj):
    if isinstance(obj, list):
        return [prepare_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: prepare_json(v) for k, v in obj.items()}
    if isinstance(obj, COQ_TYPES):
        d = {k: prepare_json(v) for k, v in zip(obj._fields, obj)}
        nm = COQ_TYPE_NAMES[type(obj).__name__]
        return {"_type": nm, **d}
    return obj

def write_json(fpath, _fname, annotated):
    with open("{}.io.json".format(fpath), mode="w") as out:
        json.dump(prepare_json(annotated), out, indent=4)

def gen_html(annotated):
    return HtmlWriter(highlight).gen_html(annotated)

def write_html(fpath, _fname, annotated):
    ts = list(gen_html(annotated))
    with open("{}.snippets.html".format(fpath), mode="w") as out:
        for t in ts:
            out.write(t.render(pretty=False))
            out.write('\n')

def write_webpage(fpath, fname, annotated):
    doc = document(title=fname)
    doc.set_attribute("class", "alectryon-standalone")

    doc.head.add(tags.meta(charset="utf-8"))
    doc.head.add(tags.meta(name="generator", content=GENERATOR))
    doc.head.add(tags.link(rel="stylesheet", href="alectryon.css"))
    doc.head.add(tags.script(src="alectryon-slideshow.js"))

    FIRA_CODE_CDN = "https://unpkg.com/firacode/distr/fira_code.css"
    doc.head.add(tags.link(rel="stylesheet", href=FIRA_CODE_CDN))

    pygments_css = FORMATTER.get_style_defs('.highlight')
    doc.head.add(tags.style(pygments_css, type="text/css"))

    container = doc.body.add(tags.article(cls="alectryon-windowed"))
    for t in gen_html(annotated):
        container.add(t)

    with open("{}.html".format(fpath), mode="w") as out:
        out.write(doc.render(pretty=False))

WRITERS = {'json': write_json, 'html': write_html, 'webpage': write_webpage}

def main():
    args = parse_arguments()
    core.DEBUG = args.debug

    try:
        for fname, chunks, pp in args.input:
            annotated = pp(annotate(chunks, args.serapi_args))
            fpath = os.path.join(args.output_directory, fname)
            WRITERS[args.writer](fpath, fname, annotated)
    except ValueError as e:
        if core.DEBUG:
            raise e
        print("Exception:", e)
        sys.exit(1)
