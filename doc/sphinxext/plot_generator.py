"""
Sphinx plugin to run example scripts and create a gallery page.

Lightly modified from the mpld3 project.

"""
from __future__ import division
import os
import re
import glob
import token
import tokenize
import shutil
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from matplotlib import image


RST_TEMPLATE = """
.. _{sphinx_tag}:

{docstring}

.. image:: {img_file}

**Python source code:** :download:`[download source: {fname}]<{fname}>`

.. literalinclude:: {fname}
    :lines: {end_line}-
"""


INDEX_TEMPLATE = """

.. raw:: html

    <style type="text/css">
    .figure {{
        position: relative;
        float: left;
        margin: 10px;
        width: 180px;
        height: 200px;
    }}

    .figure img {{
        position: absolute;
        display: inline;
        left: 0;
        width: 170px;
        height: 170px;
        opacity:1.0;
        filter:alpha(opacity=100); /* For IE8 and earlier */
    }}

    .figure:hover img {{
        -webkit-filter: blur(3px);
        -moz-filter: blur(3px);
        -o-filter: blur(3px);
        -ms-filter: blur(3px);
        filter: blur(3px);
        opacity:1.0;
        filter:alpha(opacity=100); /* For IE8 and earlier */
    }}

    .figure span {{
        position: absolute;
        display: inline;
        left: 0;
        width: 170px;
        height: 170px;
        background: #000;
        color: #fff;
        visibility: hidden;
        opacity: 0;
        z-index: 100;
    }}

    .figure p {{
        position: absolute;
        top: 45%;
        width: 170px;
        font-size: 110%;
    }}

    .figure:hover span {{
        visibility: visible;
        opacity: .4;
    }}

    .caption {{
        position: absolue;
        width: 180px;
        top: 170px;
        text-align: center !important;
    }}
    </style>

.. _{sphinx_tag}:

Example gallery
===============

{toctree}

{contents}

.. raw:: html

    <div style="clear: both"></div>
"""


BANNER_JS_TEMPLATE = """

var banner_data = {banner_data};

banner_data.forEach(function(d, i) {{
  d.i = i;
}});

var height = 150,
    width = 900,
    imageHeight = 150,
    imageWidth = 150,
    zoomfactor = 0.1;

var banner = d3.select(".example-banner");

banner.style("height", height + "px")
      .style("width", width + "px")
      .style("margin-left", "auto")
      .style("margin-right", "auto");

var svg = banner.append("svg")
                .attr("width", width + "px")
                .attr("height", height + "px");

var anchor = svg.append("g")
                  .attr("class", "example-anchor")
                .selectAll("a")
                .data(banner_data.slice(0, 7));

anchor.exit().remove();

var anchor_elements = anchor.enter().append("a")
      .attr("xlink:href", function(d) {{ return d.url; }})
      .attr("xlink:title", function(d) {{ return d.title; }});

anchor_elements.append("svg:image")
      .attr("width", (1 - zoomfactor) * imageWidth)
      .attr("height", (1 - zoomfactor) * imageHeight)
      .attr("xlink:href", function(d){{ return d.thumb; }})
      .attr("xroot", function(d){{return d3.round(imageWidth * (d.i - 0.5));}})
      .attr("x", function(d){{return d3.round(imageWidth * (d.i - 0.5));}})
      .attr("y", d3.round(0.5 * zoomfactor * imageHeight))
      .attr("i", function(d){{return d.i;}})
     .on("mouseover", function() {{
              var img = d3.select(this);
              img.transition()
                    .attr("width", imageWidth)
                    .attr("height", height)
                    .attr("x", img.attr("xroot")
                               - d3.round(0.5 * zoomfactor * imageWidth))
                    .attr("y", 0);
              }})
     .on("mouseout", function() {{
              var img = d3.select(this);
              img.transition()
                    .attr("width", (1 - zoomfactor) * imageWidth)
                    .attr("height", (1 - zoomfactor) * height)
                    .attr("x", img.attr("xroot"))
                    .attr("y", d3.round(0.5 * zoomfactor * imageHeight));
              }});
"""


def create_thumbnail(infile, thumbfile,
                     width=300, height=300,
                     cx=0.5, cy=0.5, border=4):
    baseout, extout = os.path.splitext(thumbfile)

    im = image.imread(infile)
    rows, cols = im.shape[:2]
    x0 = int(cx * cols - .5 * width)
    y0 = int(cy * rows - .5 * height)
    xslice = slice(x0, x0 + width)
    yslice = slice(y0, y0 + height)
    thumb = im[yslice, xslice]
    thumb[:border, :, :3] = thumb[-border:, :, :3] = 0
    thumb[:, :border, :3] = thumb[:, -border:, :3] = 0

    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)

    ax = fig.add_axes([0, 0, 1, 1], aspect='auto',
                      frameon=False, xticks=[], yticks=[])
    ax.imshow(thumb, aspect='auto', resample=True,
              interpolation='bilinear')
    fig.savefig(thumbfile, dpi=dpi)
    return fig


def indent(s, N=4):
    """indent a string"""
    return s.replace('\n', '\n' + N * ' ')


class ExampleGenerator(object):
    """Tools for generating an example page from a file"""
    def __init__(self, filename, target_dir):
        self.filename = filename
        self.target_dir = target_dir
        self.thumbloc = .5, .5
        self.extract_docstring()
        self.exec_file()
        with open(filename, "r") as fid:
            self.filetext = fid.read()

    @property
    def dirname(self):
        return os.path.split(self.filename)[0]

    @property
    def fname(self):
        return os.path.split(self.filename)[1]

    @property
    def modulename(self):
        return os.path.splitext(self.fname)[0]

    @property
    def pyfilename(self):
        return self.modulename + '.py'

    @property
    def rstfilename(self):
        return self.modulename + ".rst"

    @property
    def htmlfilename(self):
        return self.modulename + '.html'

    @property
    def pngfilename(self):
        pngfile = self.modulename + '.png'
        return "_images/" + pngfile

    @property
    def thumbfilename(self):
        pngfile = self.modulename + '_thumb.png'
        return pngfile

    @property
    def sphinxtag(self):
        return self.modulename

    @property
    def pagetitle(self):
        return self.docstring.strip().split('\n')[0].strip()

    @property
    def plotfunc(self):
        match = re.search(r"sns\.(.+plot)\(", self.filetext)
        if match:
            return match.group(1)
        match = re.search(r"sns\.(.+Grid)\(", self.filetext)
        if match:
            return match.group(1)
        return ""

    def extract_docstring(self):
        """ Extract a module-level docstring
        """
        lines = open(self.filename).readlines()
        start_row = 0
        if lines[0].startswith('#!'):
            lines.pop(0)
            start_row = 1

        docstring = ''
        first_par = ''
        tokens = tokenize.generate_tokens(lines.__iter__().next)
        for tok_type, tok_content, _, (erow, _), _ in tokens:
            tok_type = token.tok_name[tok_type]
            if tok_type in ('NEWLINE', 'COMMENT', 'NL', 'INDENT', 'DEDENT'):
                continue
            elif tok_type == 'STRING':
                docstring = eval(tok_content)
                # If the docstring is formatted with several paragraphs,
                # extract the first one:
                paragraphs = '\n'.join(line.rstrip()
                                       for line in docstring.split('\n')
                                       ).split('\n\n')
                if len(paragraphs) > 0:
                    first_par = paragraphs[0]
            break

        thumbloc = None
        for i, line in enumerate(docstring.split("\n")):
            m = re.match(r"^_thumb: (\.\d+),\s*(\.\d+)", line)
            if m:
                thumbloc = float(m.group(1)), float(m.group(2))
                break
        if thumbloc is not None:
            self.thumbloc = thumbloc
            docstring = "\n".join([l for l in docstring.split("\n")
                                   if not l.startswith("_thumb")])

        self.docstring = docstring
        self.short_desc = first_par
        self.end_line = erow + 1 + start_row

    def exec_file(self):
        print("running {0}".format(self.filename))

        plt.close('all')
        my_globals = {'pl': plt,
                      'plt': plt}
        execfile(self.filename, my_globals)

        fig = plt.gcf()
        fig.canvas.draw()
        pngfile = os.path.join(self.target_dir,
                               self.pngfilename)
        thumbfile = os.path.join("example_thumbs",
                                 self.thumbfilename)
        self.html = "<img src=../%s>" % self.pngfilename
        fig.savefig(pngfile, dpi=75)

        cx, cy = self.thumbloc
        create_thumbnail(pngfile, thumbfile, cx=cx, cy=cy)

    def toctree_entry(self):
        return "   ./%s\n\n" % os.path.splitext(self.htmlfilename)[0]

    def contents_entry(self):
        return (".. raw:: html\n\n"
                "    <div class='figure align-center'>\n"
                "    <a href=./{0}>\n"
                "    <img src=../_static/{1}>\n"
                "    <span class='figure-label'>\n"
                "    <p>{2}</p>\n"
                "    </span>\n"
                "    </a>\n"
                "    </div>\n\n"
                "\n\n"
                "".format(self.htmlfilename,
                          self.thumbfilename,
                          self.plotfunc))


def main(app):
    static_dir = os.path.join(app.builder.srcdir, '_static')
    target_dir = os.path.join(app.builder.srcdir, 'examples')
    image_dir = os.path.join(app.builder.srcdir, 'examples/_images')
    thumb_dir = os.path.join(app.builder.srcdir, "example_thumbs")
    source_dir = os.path.abspath(os.path.join(app.builder.srcdir,
                                              '..', 'examples'))
    if not os.path.exists(static_dir):
        os.makedirs(static_dir)

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    if not os.path.exists(image_dir):
        os.makedirs(image_dir)

    if not os.path.exists(thumb_dir):
        os.makedirs(thumb_dir)

    if not os.path.exists(source_dir):
        os.makedirs(source_dir)

    banner_data = []

    toctree = ("\n\n"
               ".. toctree::\n"
               "   :hidden:\n\n")
    contents = "\n\n"

    # Write individual example files
    for filename in glob.glob(os.path.join(source_dir, "*.py")):
        ex = ExampleGenerator(filename, target_dir)

        banner_data.append({"title": ex.pagetitle,
                            "url": os.path.join('examples', ex.htmlfilename),
                            "thumb": os.path.join(ex.thumbfilename)})
        shutil.copyfile(filename, os.path.join(target_dir, ex.pyfilename))
        output = RST_TEMPLATE.format(sphinx_tag=ex.sphinxtag,
                                     docstring=ex.docstring,
                                     end_line=ex.end_line,
                                     fname=ex.pyfilename,
                                     img_file=ex.pngfilename)
        with open(os.path.join(target_dir, ex.rstfilename), 'w') as f:
            f.write(output)

        toctree += ex.toctree_entry()
        contents += ex.contents_entry()

    if len(banner_data) < 10:
        banner_data = (4 * banner_data)[:10]

    # write index file
    index_file = os.path.join(target_dir, 'index.rst')
    with open(index_file, 'w') as index:
        index.write(INDEX_TEMPLATE.format(sphinx_tag="example_gallery",
                                          toctree=toctree,
                                          contents=contents))

    # write javascript include for front page
    js_file = os.path.join(static_dir, 'banner_data.js')
    with open(js_file, 'w') as js:
        js.write(BANNER_JS_TEMPLATE.format(
            banner_data=json.dumps(banner_data)))


def setup(app):
    app.connect('builder-inited', main)
