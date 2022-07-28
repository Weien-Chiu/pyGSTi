"""
Front end Kivy widget display classes, created by objects in the kivywidget render type.

Placed in a separate module so we don't import them (and import from kivy) unless necessary,
i.e., within a GUI front end, as importing kivy within other contexts (e.g. a jupyter notebook)
can cause graphics problems and system lockup.
"""
#***************************************************************************************************
# Copyright 2015, 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains certain rights
# in this software.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License.  You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0 or in the LICENSE file in the root pyGSTi directory.
#***************************************************************************************************

import numpy as _np
import json as _json
import warnings as _warnings
import xml.etree.ElementTree as _ET
from scipy.stats import chi2 as _chi2

try:
    import latextools as _latextools
except ImportError:
    _latextools = None

try:
    from kivy.uix.widget import Widget
    from kivy.uix.gridlayout import GridLayout
    from kivy.uix.anchorlayout import AnchorLayout
    from kivy.uix.scatter import Scatter
    from kivy.uix.label import Label
    #from kivy.uix.image import Image
    from kivy.clock import Clock
    from kivy.core.window import Window

    from kivy.graphics import Color, Rectangle, Line
    from kivy.graphics.svg import Svg
    from kivy.graphics.transformation import Matrix
    from kivy.graphics.context_instructions import Translate

    from kivy.properties import NumericProperty, ListProperty
except ImportError:
    GridLayout = object


class TableWidget(GridLayout):

    background_color = ListProperty([0.9, 0.9, 0.9, 1])
    line_color = ListProperty([0.2, 0.2, 0.2, 1])
    line_thickness = NumericProperty(2.0)

    def __init__(self, formatted_headings, formatted_rows, spec, **kwargs):
        super(TableWidget, self).__init__(**kwargs)
        self.rows = len(formatted_rows) + 1  # +1 for column headers
        self.cols = len(formatted_headings)
        self.padding = 2
        self.spacing = 5

        # Set size hints for CellWidgetContainers (direct children of
        # this table (a GridLayout) to proportion grid correctly, based
        # on overall row widths and heights relative to table width & height.
        # Set size hints of cell contents to proportion content size relative
        # to cell size.

        #Note: sizes (widths and heights) of cell contents are set BUT these widgets
        # were not created with size_hint=(None,None), so they *will* be resized by
        # their parent later on (this is desirable).  The current sizes just serve as
        # "natural sizes" in order to correctly proportion the table we're now building.

        print("TABLE SIZE COMPUTE:")

        # pass 1: get row and column widths and heights - don't add any widgets yet
        heading_row_width = sum([w.content.width for w in formatted_headings])
        heading_row_height = max([w.content.height for w in formatted_headings])

        row_widths = [sum([w.content.width for w in row]) for row in formatted_rows]
        row_heights = [max([w.content.height for w in row]) for row in formatted_rows]

        ncols = len(formatted_headings)
        if ncols > 0:
            column_widths = [max(max([r[j].content.width for r in formatted_rows]), formatted_headings[j].content.width)
                             for j in range(ncols)]
            column_heights = [(sum([r[j].content.height for r in formatted_rows]) + formatted_headings[j].content.height)
                              for j in range(ncols)]
        else:
            column_widths = column_heights = []

        table_width = sum(column_widths)  # max(max(row_widths), heading_row_width)
        table_height = sum(row_heights) + heading_row_height
        print("col_widths =", column_widths)
        print("row_heights =", row_heights)
        print("heading row height = ", heading_row_height)
        assert(len(column_heights) == 0 or table_height >= max(column_heights))  # can have all columns less than table height b/c of row alighmt

        # pass 2: add widgets and set their size hints
        for colwidth, heading_cell_widget in zip(column_widths, formatted_headings):
            heading_cell_widget.size_hint_x = colwidth / table_width
            heading_cell_widget.size_hint_y = heading_row_height / table_height
            heading_cell_widget.content.size_hint_x = heading_cell_widget.content.width / colwidth
            heading_cell_widget.content.size_hint_y = heading_cell_widget.content.height / heading_row_height
            self.add_widget(heading_cell_widget)

        for rowheight, row in zip(row_heights, formatted_rows):
            assert(len(row) == self.cols)
            for colwidth, cell_widget in zip(column_widths, row):
                cell_widget.size_hint_x = colwidth / table_width
                cell_widget.size_hint_y = rowheight / table_height
                cell_widget.content.size_hint_x = cell_widget.content.width / colwidth
                cell_widget.content.size_hint_y = cell_widget.content.height / rowheight
                #if isinstance(cell_widget.content, LatexWidget):
                #    print('**** ', cell_widget.content.latex_string)
                #    print(f'{cell_widget.content.width=} {colwidth=}')
                #    print(f'{cell_widget.content.height=} {colwidth=}')
                #    print("Size hint = ", cell_widget.content.size_hint_x, cell_widget.content.size_hint_y)
                #    print("")
                self.add_widget(cell_widget)

        with self.canvas.before:
            self._bgcolor = Color(*self.background_color)
            self.bind(background_color=lambda instr, value: setattr(self._bgcolor, "rgba", value))
            self._bgrect = Rectangle(pos=self.pos, size=self.size)

            self._lncolor = Color(*self.line_color)
            self.bind(line_color=lambda instr, value: setattr(self._lncolor, "rgba", value))

            self._lines = Line(points=[], width=self.line_thickness, joint='round')
            self.bind(line_thickness=lambda instr, value: setattr(self._lines, "width", value))

        self.bind(pos=self._redraw, size=self._redraw)

        self.size = (table_width, table_height)

        self._trigger = Clock.create_trigger(self._redraw)
        self._trigger()  # trigger _redraw call on *next* clock cycle, when children will have computed positions
        print("DB: TABLE Initial size = ", self.size)

    def _redraw(self, *args):
        print("Table redraw", id(self), 'pos', self.pos)
        #Update background rectangle
        self._bgrect.pos = self.pos
        self._bgrect.size = self.size

        #DEBUG REMOVE
        #for c in self.children:
        #    print(c.pos, '    ', c.size)

        #Update lines
        # Note self.children is in reverse order of additions,
        # so top row is at end (?)
        cells_in_added_order = list(reversed(self.children))
        top_row = cells_in_added_order[0:self.cols]
        first_col = cells_in_added_order[-1::-self.cols]  # reverse so ys are ascending

        xs = [top_row[0].x]; end = top_row[0].x + top_row[0].width
        for c in top_row[1:]:
            xs.append((c.x + end) / 2.0)
            end = c.x + c.width
        xs.append(end)
        print("xs = ", xs)

        ys = [first_col[0].y]; end = first_col[0].y + first_col[0].height
        for c in first_col[1:]:
            ys.append((c.y + end) / 2.0)
            end = c.y + c.height
        ys.append(end)
        print("ys = ", ys)

        #patch: always use self's position and size for border lines
        xs[0] = self.x; xs[-1] = self.x + self.width
        ys[0] = self.y; ys[-1] = self.y + self.height

        pts = []
        ybegin, yend = ys[0], ys[-1]
        for x in xs:
            pts.extend([x, ybegin, x, yend])  # horizontal line
            ybegin, yend = yend, ybegin

        # done with horizontal lines; current point is at xs[-1], ybegin
        xbegin, xend = xs[-1], xs[0]
        ys_iter = ys if (ybegin == ys[0]) else reversed(ys)
        for y in ys_iter:
            pts.extend([xbegin, y, xend, y])  # vertical line
            xbegin, xend = xend, xbegin

        self._lines.points = pts


class CellWidgetContainer(AnchorLayout):
    def __init__(self, widget, hover_text=None):
        super().__init__()  # no kwargs plumbed through
        self.content = widget
        self.add_widget(self.content)
        self.hover_text = hover_text
        if hover_text is not None:
            Window.bind(mouse_pos=self.on_mouse_pos)
        self._sidebar_layout = self._status_label = None

        # Uncomment these lines and refs to bgrect below to show green CellWidgetContainer background area (for debugging)
        #with self.canvas.before:
        #    Color(0.0, 0.7, 0.0)
        #    self.bgrect = Rectangle(pos=(0,0), size=self.size)

    def set_info_containers(self, sidebar_layout, status_label):
        self._sidebar_layout = sidebar_layout
        self._status_label = status_label

    def on_mouse_pos(self, *args):
        if not self.get_root_window():
            return  # don't proceed if I'm not displayed <=> If have no parent

        pos = args[1]
        tpos = self.to_widget(*pos, relative=False)  # because pos is in window coords
        if self.collide_point(*tpos) and self._status_label:
            self._status_label.text = self.hover_text

    def on_size(self, *args):
        #print("Cell container onsize: ", self.size)
        #self.bgrect.pos = self.pos
        #self.bgrect.size = self.size
        pass


class LatexWidget(Scatter):
    svg_cache = None

    @classmethod
    def write_cache(cls, filename):
        to_save = {}
        if cls.svg_cache is None: return
        for key, etree in cls.svg_cache.items():
            to_save[key] = _ET.tostring(etree._root).decode("utf-8")
        with open(filename, 'w') as f:
            _json.dump(to_save, f)

    @classmethod
    def read_cache(cls, filename):
        cls.svg_cache = {}
        with open(filename) as f:
            d = _json.load(f)
        for key, etree_str in d.items():
            cls.svg_cache[key] = _ET.ElementTree(_ET.fromstring(etree_str.encode('utf-8')))

    def __init__(self, latex_string): # , **kwargs):
        kwargs = {}
        kwargs.update(dict(do_rotation=False, do_scale=False, do_translation=False))
        super(LatexWidget, self).__init__(**kwargs)
        self.latex_string = latex_string

        if self.svg_cache is None or latex_string not in self.svg_cache:
            print("*** LATEX CACHE MISS ***")
            if _latextools is None:
                raise ValueError(("You must `pip install latextools` and `pip install drawSvg`"
                                  " to render latex within Kivy widgets."))
            print("LATEX RENDERING: \n",latex_string)
            pdf = _latextools.render_snippet(latex_string, commands=[_latextools.cmd.all_math])
            svg_string = pdf.as_svg().content

            #This manual SVG simplification/manipulation should be unnecessary once Svg() in Kivy works properly
            svg_string = svg_string.replace(r'0%', '0')  # used, e.g. in 'rgb(0%,0%,0%)'
            svg_string = svg_string.replace(r'stroke:none', 'stroke-width:0')  # because 'stroke:none' doesn't work (I think because the alpha channel doesn't)
            etree = _ET.ElementTree(_ET.fromstring(svg_string))
            etree = self.simplify_svg_tree(etree)
            if self.svg_cache is not None: self.svg_cache[latex_string] = etree
        else:
            print("*** LATEX CACHE HIT ***")
            etree = self.svg_cache[latex_string]

        with self.canvas:
            self.svg_offset = Translate(0, 0)
            svg = Svg()
            svg.set_tree(etree)

        # Uncomment these lines and refs to bgrect below to show yellow LatexWidget background area (for debugging)
        #with self.canvas.before:
        #    Color(0.7, 0.7, 0.0)  # dark yellow
        #    self.bgrect = Rectangle(pos=(0,0), size=self.size)

        #self.etree = etree
        self.svg_size = (svg.width, svg.height)
        #print("SVG size = ", svg.width, svg.height)
        SCALE_FACTOR = 4
        self.size = SCALE_FACTOR * svg.width, SCALE_FACTOR * svg.height

        #REMOVE
        #desired_width = 200.0
        #desired_height = 200.0
        #scalew = desired_width / svg.width  # so scale * svg_width == desired_width
        #scaleh = desired_height / svg.height  # so scale * svg_width == desired_width
        #self.scale = 1.0  #min(scalew, scaleh)
        #print("Scatter size = ", self.size) #, " scale=", self.scale)

        # An alternative to SVG mode above -- very slow and less good -- REMOVE
        #if image_mode:
        #    with _tempfile.TemporaryDirectory() as tmp:
        #        #import bpdb; bpdb.set_trace()
        #        temp_filename = _os.path.join(tmp, 'kivy-latex-widget-image.png')
        #        pdf.rasterize(temp_filename, scale=10)  # returns a raster obj
        #        with self.canvas:
        #            self.img = Image(source=temp_filename)
        #    self.size = self.img.width, self.img.height

    def on_size(self, *args):
        #self.canvas.before.clear()
        #print("Latex onsize ", self.pos, self.size, ' :: ', self.latex_string)
        scalew = self.size[0] / self.svg_size[0]  # so scale * svg_width == desired_width
        scaleh = self.size[1] / self.svg_size[1]  # so scale * svg_width == desired_width
        if scalew <= scaleh:  # scale so width of SVG takes up full width; center in y
            self.scale = max(scalew, 0.01)  # don't allow scale == 0 (causes error)
            self.svg_offset.x = 0
            self.svg_offset.y = (self.size[1] / self.scale - self.svg_size[1]) / 2.0
        else:  # scale so height of SVG takes up full height; center in x
            self.scale = max(scaleh, 0.01)  # don't allow scale == 0 (causes error)
            self.svg_offset.x = (self.size[0] / self.scale - self.svg_size[0]) / 2.0
            self.svg_offset.y = 0
        #print("  -> Latex scale = ",self.scale)

        #self.bgrect.pos = (0, 0)  # not self.pos -- these are relative coords to Scatter's context
        #self.bgrect.size = (self.size[0] / self.scale, self.size[1] / self.scale)  # coords *before* scaling

    def simplify_svg_tree(self, svg_tree):
        """" Simplifies - mainly by resolving reference links within - a SVG file so that Kivy can digest it """
        definitions = {}

        def simplify_element(e, new_parent, definitions, in_defs):
            if e.tag.endswith('svg'):
                assert(new_parent is None), "<svg> element shouldn't have any parent (should be the root)!"
                if e.get('viewBox', None):
                    definitions['_viewbox'] = e.get('viewBox')  # perhaps for later use

                # Remove "pt" unit designations from width and height, as Kivy doesn't understand that
                # this sets the units for the entire file, and treats the rest of the file's number as
                # being in pixels -- so removing the "pt"s causes everything to be in pixels.
                attrib = e.attrib.copy()
                if 'width' in attrib and attrib['width'].endswith('pt'):
                    attrib['width'] = attrib['width'][:-2]
                if 'height' in attrib and attrib['height'].endswith('pt'):
                    attrib['height'] = attrib['height'][:-2]
                new_el = _ET.Element(e.tag, attrib)
                process_children = True
            elif in_defs and e.tag.endswith('symbol'):
                if e.get('id', None) is not None:
                    new_el = _ET.Element(e.tag, e.attrib)  # root a new "symbol tree" w/out parent
                    definitions[e.get('id')] = new_el
                    process_children = True
                else:  # no id, so ignore
                    _warnings.warn("SVG definition without id!")
                    new_el = None
                    process_children = False
            elif e.tag.endswith('clipPath'):  # ignore clipPath (Kivy can't process it)
                new_el = None
                process_children = False
            elif e.tag.endswith('defs'):
                in_defs = True
                new_el = None  # Ignore defs tag, but still process children
                process_children = True
            elif e.tag.endswith('use'):
                href = e.get('href', None)
                if href is None:  # then try to look for a {url}href attribute:
                    for k, v in e.attrib.items():
                        if k.endswith('href'):
                            href = v; break
                if href.startswith('#') and href[1:] in definitions:
                    href = href[1:]
                    new_el = _ET.SubElement(new_parent, 'g',
                                            {'transform': 'translate(%s,%s)' % (e.get('x', '0'), e.get('y', '0'))})
                    use_root = definitions[href]
                    for child_el in use_root:
                        simplify_element(child_el, new_el, definitions, False)
                else:
                    _warnings.warn("SVG id=%s not found in defintions" % str(href))
                    new_el = None  # id not found or not in defintions
                process_children = False
            # REMOvE: no need to perform any special processing here
            #elif e.tag.endswith('g'):
            #    if e.get('clip-path', None):
            #        new_el = new_parent  # ignore g elements with clip-path
            #    else:  # process normally
            #        new_el = 'copy'
            #    process_children = True
            else:
                new_el = 'copy'
                process_children = True

            if new_el == 'copy':
                new_el = _ET.Element(e.tag, e.attrib) if (new_parent is None) \
                    else _ET.SubElement(new_parent, e.tag, e.attrib)

            # DEBUG HACK - create RED bounding box for debugging -- REMOVE LATER
            #if e.tag.endswith('svg'):
            #    if e.get('viewBox', None):
            #        x, y, w, h = e.get('viewBox').split()
            #        #print("SVG bouding box dimensions: ",w, h)
            #        _ET.SubElement(new_el, 'path',
            #                       {'stroke': 'red', 'fill': 'none',
            #                        'd': "M {x0} {y0} L {x1} {y0} L {x1} {y1} L {x0} {y1} Z".format(
            #                            x0=x, y0=y, x1=str(float(x) + float(w)), y1=str(float(y) + float(h)))})

            if process_children:
                for child_el in e:
                    simplify_element(child_el, new_el, definitions, in_defs)

            return new_el

        root = svg_tree._root  # SVG root element
        new_root = simplify_element(root, None, definitions, False)
        # Note: could use definitions['_viewbox'] here if needed
        return _ET.ElementTree(new_root)


class WrappedLabel(Label):
    def __init__(self, *args, **kwargs):
        kwargs['size_hint_y'] = None
        super().__init__(*args, **kwargs)
        self.bind(
            width=lambda *x: self.setter('text_size')(self, (self.width, None)),
            texture_size=lambda *x: self.setter('height')(self, self.texture_size[1]))


class AdjustingLabel(Label):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.size_map = {}  # maps font size -> texture size  (FUTURE performance enhancement)
        self.texture_update()
        if self.texture_size[1] <= 0:
            return

        aspect = self.texture_size[0] / self.texture_size[1]
        self.width = self.texture_size[0]
        print("Aspect = ",aspect, ' width=', self.width, 'text_size=', self.text_size, 'text=', self.text)

        if aspect > 10:
            self.size_hint_y = None
            width = self.texture_size[0]
            while aspect > 10:
                width *= 0.75
                self.text_size = width, None
                self.texture_update()
                aspect = self.texture_size[0] / self.texture_size[1]
                print("Aspect = ",aspect, 'texture_size = ', self.texture_size)
        else:
            self.size_hint_y = None
            self.text_size = self.texture_size[0], None

        self.aspect = aspect

        #self.bind(
        #    width=lambda *x: self.setter('text_size')(self, (self.width, None)),
        #    texture_size=lambda *x: self.setter('height')(self, self.texture_size[1]))

    def on_size(self, *args):
        # Change font size until texture size is correct
        font_size_min = 6
        font_size_max = 40
        PADDING = 10
        TOLPADDING = 20
        initial_font_size = self.font_size

        if self.text_size[0] is None or self.texture_size[0] is None or self.texture_size[1] is None:
            return  # exit right away if label hasn't been initialized yet.

        # Check if we need to adjust self.text_size to match our width
        if self.width < self.text_size[0] + 2 * PADDING - TOLPADDING:
            self.text_size = self.width - 2 * PADDING, None  # force texture width to be ok
            self.texture_update()
        elif self.width > self.text_size[0] + 2 * PADDING + TOLPADDING:
            self.text_size = self.width - 2 * PADDING, None  # force texture width to be ok
            self.texture_update()

        #Adjust font size so texture height is <= our height
        if self.height < self.texture_size[1] + 2 * PADDING - TOLPADDING:
            while self.texture_size[1] > self.height - 2 * PADDING and self.font_size > font_size_min:
                self.font_size -= 2
                self.texture_update()
        elif self.height > self.texture_size[1] + 2 * PADDING + TOLPADDING:
            last_acceptable_font_size = self.font_size
            while self.texture_size[1] < self.height - 2 * PADDING and self.font_size < font_size_max:
                last_acceptable_font_size = self.font_size
                self.font_size += 2
                self.texture_update()
            if self.font_size != last_acceptable_font_size:
                self.font_size = last_acceptable_font_size
                self.texture_update()

        if initial_font_size != self.font_size:
            print(f"AdjustingLabel on_size updated font size: {initial_font_size} -> {self.font_size}")

    #OLD REMOVE:
    #    if self.text_size[0] != self.width:  # avoids recusive on_size calls
    #        #print("On size: ", self.size, ' texture', self.texture_size)
    #        self.text_size = self.width, None
    #        self.height = self.texture_size[1]  # prompts another on_size call...


def _build_kivy_color_scatterplot(xs, ys, ylabel, colormap, **kwargs):
    from pygsti.report.kivygraph import Graph, PointPlot
    graph_theme = {
        'label_options': {
            'color': (0, 0, 0, 1),  # color of tick labels and titles
            'bold': False},
        'background_color': (1, 1, 1, 1),  # canvas background color
        'tick_color': (0, 0, 0, 0),  # ticks and grid
        'border_color': (0, 0, 0, 1),  # border drawn around each graph
        'font_size': 18
    }
    kwargs.update(graph_theme)

    xmax = max(xs)
    yrng = max(ys) - min(ys)
    colors = [colormap.interpolate_color_tuple(y, rgb_ints=False, add_alpha=1.0) for y in ys]
    graph = Graph(xlabel='Circuit Depth', ylabel='' if (ylabel is None) else ylabel,
                  x_ticks_major=10**(_np.floor(_np.log10(xmax)) - 1), x_ticks_minor=2,
                  y_ticks_major=10**(_np.floor(_np.log10(yrng)) - 1), y_ticks_minor=2,
                  y_grid_label=True, x_grid_label=True, padding=5,
                  x_grid=True, y_grid=True, xmin=0, xmax=xmax,
                  ymin=float(min(ys)), ymax=float(max(ys)),  # float needed b/c Graph doesn't like numpy types
                  **kwargs)
    plot = PointPlot(color=[0, 0.5, 0, 1], point_size=5.0, colors=colors)
    plot.points = list(zip(xs, ys))
    graph.add_plot(plot)
    return graph


def _build_kivy_color_histogram(bin_edges, hist_values, bin_centers, ylabel, nvals, bindelta,
                                dof, minlog, maxlog, colors, **kwargs):
    from pygsti.report.kivygraph import Graph, BarPlot, SmoothLinePlot
    hist_values = [(10**(minlog - 1) if v <= 0 else v) for v in hist_values]
    xrng = bin_edges[-1] - bin_edges[0]
    #yrng = 10**maxlog - 10**minlog

    graph_theme = {
        'label_options': {
            'color': (0, 0, 0, 1),  # color of tick labels and titles
            'bold': False},
        'background_color': (1, 1, 1, 1),  # canvas background color
        'tick_color': (0, 0, 0, 1),  # ticks and grid
        'border_color': (0, 0, 0, 1),  # border drawn around each graph
        'font_size': 18
    }
    kwargs.update(graph_theme)

    graph = Graph(xlabel=ylabel, ylabel='counts',
                  x_ticks_major=round(xrng / 10, 0),  # 10**(_np.floor(_np.log10(xrng)) - 1),
                  y_ticks_major=0.2,  # 10**(_np.floor(_np.log10(yrng)) - 1),
                  x_grid_label=True, y_grid_label=True, padding=5,
                  x_grid=True, y_grid=True,
                  xmin=float(bin_edges[0]), xmax=float(bin_edges[-1]),
                  ylog=True, ymin=float(10**minlog), ymax=float(10**maxlog), **kwargs)
    bplot = BarPlot(color=(0, 0, 0.5, 1), bar_spacing=1.0, colors=colors)
    graph.add_plot(bplot)
    bplot.bind_to_graph(graph)
    bplot.points = [(x, y) for x, y in zip(bin_edges[:-1], hist_values)]

    lplot = SmoothLinePlot(color=(0.1, 0.1, 0.1, 1))
    lplot.points = [(x, nvals * bindelta * _chi2.pdf(x, dof)) for x in bin_centers]
    graph.add_plot(lplot)

    return graph


def _build_kivy_choi_barplot(xvals, yvals, **kwargs):
    from pygsti.report.kivygraph import Graph, BarPlot
    graph = Graph(xlabel='index',  #ylabel='Y',
                  x_ticks_major=1.0, y_ticks_major=1.0,
                  y_grid_label=True, x_grid_label=True, padding=5,
                  x_grid=True, y_grid=True, xmin=0, xmax=len(xvals) + 1, ymin=0, ymax=1,
                  **kwargs)
    plot = BarPlot(color=(0, 0, 1, 1), bar_spacing=.5)
    graph.add_plot(plot)
    plot.bind_to_graph(graph)
    plot.points = [(x, abs(y)) for x, y in zip(xvals, yvals)]
    return graph


def _build_kivy_fitcompare_barplot(xs_to_plot, ys_to_plot, colors, xlabel, **kwargs):
    from pygsti.report.kivygraph import Graph, BarPlot
    xrng = max(xs_to_plot) - min(xs_to_plot)
    #yrng = max(ys_to_plot) - min(ys_to_plot)

    graph_theme = {
        'label_options': {
            'color': (0, 0, 0, 1),  # color of tick labels and titles
            'bold': False},
        'background_color': (1, 1, 1, 1),  # canvas background color
        'tick_color': (0, 0, 0, 1),  # ticks and grid
        'border_color': (0, 0, 0, 1),  # border drawn around each graph
        'font_size': 18
    }
    kwargs.update(graph_theme)

    graph = Graph(xlabel=xlabel if xlabel else '', ylabel='N_sigma',
                  x_ticks_major=round(xrng / 10, 0),
                  y_ticks_major=0.2,
                  x_grid_label=True, y_grid_label=True, padding=5,
                  x_grid=True, y_grid=True,
                  xmin=float(min(xs_to_plot)), xmax=float(max(xs_to_plot)),
                  ylog=True, ymin=float(min(ys_to_plot)), ymax=float(max(ys_to_plot)), **kwargs)
    bplot = BarPlot(color=(0, 0, 0.5, 1), bar_spacing=1.0, colors=colors)
    graph.add_plot(bplot)
    bplot.bind_to_graph(graph)
    bplot.points = [(x, y) for x, y in zip(xs_to_plot, ys_to_plot)]

    return graph
