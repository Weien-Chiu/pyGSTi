from kivy.app import App

from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.stacklayout import StackLayout
from kivy.uix.scatterlayout import ScatterLayout
from kivy.uix.relativelayout import RelativeLayout
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.splitter import Splitter
from kivy.uix.dropdown import DropDown
from kivy.uix.treeview import TreeView, TreeViewLabel
from kivy.uix.spinner import Spinner
from kivy.uix.behaviors import DragBehavior
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.accordion import Accordion, AccordionItem
from kivy.uix.modalview import ModalView
from kivy.uix.stencilview import StencilView
from kivy.uix.popup import Popup
from kivy.clock import Clock

from kivy.properties import ObjectProperty, StringProperty
from kivy.graphics import Color, Rectangle, Line

from .kivyresize import ResizableBehavior
from .kivygraph import Graph, MeshLinePlot, BarPlot, MatrixBoxPlotGraph

import pygsti
from pygsti.report import Workspace
from pygsti.protocols.gst import ModelEstimateResults as _ModelEstimateResults
from pygsti.protocols.gst import StandardGSTDesign as _StandardGSTDesign
from pygsti.io import read_results_from_dir as _read_results_from_dir
from pygsti.objectivefns import objectivefns as _objfns

from kivy.core.window import Window
Window.size = (1200, 700)


def set_info_containers(root, sidebar, statusbar):
        """Walk down widget tree from `root` and call `set_info_containers` on applicable children. """
        if hasattr(root, 'set_info_containers'):
            root.set_info_containers(sidebar, statusbar)
        for c in root.children:
            set_info_containers(c, sidebar, statusbar)


class RootExplorerWidget(BoxLayout):

    def __init__(self, results_dir, **kwargs):
        self.results_dir = results_dir
        super().__init__(**kwargs)
        Clock.schedule_once(self.after_created, 0)

    def after_created(self, delta_time):
        print("Running post-kv-file creation of root widget.")
        self.ids.create_tab.add_widget(self.create_add_item_panel(self.ids.center_area))
        set_info_containers(self.ids.center_area, self.ids.info_layout, self.ids.status_label)

    def create_add_item_panel(self, center_area):

        items_by_category = {
            '-- Model Violation --': ['FitComparisonTable', 'ColorBoxPlot', 'ColorScatterPlot', 'ColorHistogramPlot',
                                      'FitComparisonBarPlot', 'FitComparisonBoxPlot'],
            '-- G. Inv. Metrics --': ['SpamParametersTable', 'GateEigenvalueTable', 'ModelVsTargetTable',
                                      'WildcardBudgetTable'],
            '-- Metrics --': ['GatesVsTargetTable', 'SpamVsTargetTable', 'SpamTable', 'GatesTable', 'ChoiTable',
                              'ErrgenTable', 'NQubitErrgenTable', 'GateDecompTable', 'GatesSingleMetricTable'],
            '-- Reference --': ['CircuitTable', 'DataSetOverviewTable', 'StandardErrgenTable', 'GaugeOptParamsTable',
                                'MetadataTable', 'SoftwareEnvTable'],
        }
        # 'GateMatrixPlot', 'MatrixPlot', DatasetComparisonHistogramPlot, RandomizedBenchmarkingPlot
        # GaugeRobustModelTable, GaugeRobustMetricTable, GaugeRobustErrgenTable, ProfilerTable

        first_child = None
        ret = Accordion(orientation='vertical', height=1000)  # height= just to try to supress initial warning
        for category_name, item_list in items_by_category.items():
            acc_item = CustomAccordionItem(title=category_name)
            acc_item_layout = BoxLayout(orientation='vertical')
            acc_item.add_widget(acc_item_layout)
            for item_name in item_list:
                btn = Button(text=item_name, size_hint_y=None, height=50)  # must specify height manually
                btn.bind(on_press=lambda btn: center_area.add_item(btn.text))  # pressing button fires add_item
                acc_item_layout.add_widget(btn)
            acc_item_layout.add_widget(Label(text=''))  # blank variable-height label so buttons are at top of BoxLayout
            ret.add_widget(acc_item)
            if first_child is None: first_child = acc_item

        ret.select(first_child)
        return ret

    def dismiss_popup(self):
        self._popup.dismiss()

    def show_load(self):
        content = LoadDialog(load=self.load, cancel=self.dismiss_popup)
        self._popup = Popup(title="Load file", content=content,
                            size_hint=(0.9, 0.9))
        self._popup.open()

    def load(self, path, filename):
        print("TODO: load root: ", path, filename)
        self.dismiss_popup()


class TreeViewLabelWithData(TreeViewLabel):
    def __init__(self, data, **kwargs):
        super().__init__(**kwargs)
        self.data = data


class BorderedBoxLayout(BoxLayout):
    def __init__(self, **kwargs):
        if 'border_width' in kwargs:
            self.border_thickness = kwargs['border_thickness']
            del kwargs['border_thickness']
        else:
            self.border_thickness = 4
        super().__init__(**kwargs)

        with self.canvas.after:
            Color(0.5, 0.5, 0.5)
            self.border_rect = Line(points=[], width=self.border_thickness)
        self._update_border()
        self.bind(size=self._update_border, pos=self._update_border)

    def _update_border(self, *args):
        t = self.border_thickness
        x1, y1 = self.x + t, self.y + t
        x2, y2 = self.x + self.width - t, self.y + self.height - t
        self.border_rect.points = [x1, y1, x2, y1, x2, y2, x1, y2, x1, y1]


class ResultsDirSelectorWidget(BorderedBoxLayout):
    # allows selection of edesign (tree node) and results object (dataset) at tree node.
    root_results_dir = ObjectProperty(None, allownone=True)
    selected_results_dir = ObjectProperty(None, allownone=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def on_root_results_dir(self, inst, val):
        tv = TreeView(root_options=dict(text='Root <filename>'),
                      hide_root=False,
                      indent_level=4)

        def populate_view(results_dir, parent_tree_node):
            for ky in results_dir.keys():
                tree_node = tv.add_node(TreeViewLabelWithData(data=results_dir[ky], text=str(ky), is_open=True),
                                        parent=parent_tree_node)  # OK if None
                populate_view(results_dir[ky], tree_node)

        populate_view(self.root_results_dir, None)
        tv.bind(_selected_node=self.on_change_selected_node)

        self.clear_widgets()
        self.add_widget(tv)
        tv.select_node(tv.get_root())

    def on_change_selected_node(self, instance, new_node):
        print("Selected results dir: " + new_node.text)  # (new_node is a TreeViewLabel)
        self.selected_results_dir = new_node.data if hasattr(new_node, 'data') else self.root_results_dir


class ResultsSelectorWidget(BorderedBoxLayout):
    results_dir_selector_widget = ObjectProperty(None, allownone=True)
    selected_results = ObjectProperty(None, allownone=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.treeview = None

    def on_results_dir_selector_widget(self, inst, val):
        if self.results_dir_selector_widget is None: return
        if self.results_dir_selector_widget.selected_results_dir is not None:
            self.refresh_tree(self.results_dir_selector_widget.selected_results_dir)                
        self.results_dir_selector_widget.bind(selected_results_dir=self.on_change_selected_results_dir)

    def refresh_tree(self, current_results_dir):
        tv = TreeView(root_options=dict(text='Results for protocol:'),
                      hide_root=False,
                      indent_level=4)

        def populate_view(results_dir, parent_tree_node):
            for protocol_name, protocol_results in results_dir.for_protocol.items():
                tv.add_node(TreeViewLabelWithData(data=protocol_results, text=str(protocol_name),
                                                  is_open=True), parent=parent_tree_node)  # OK if None

        populate_view(current_results_dir, None)
        tv.bind(_selected_node=self.on_change_selected_node)

        self.treeview = tv
        self.clear_widgets()
        self.add_widget(tv)

    def on_change_selected_node(self, instance, new_node):
        if new_node.is_leaf:
            print("Selected Results: " + new_node.text)  # (new_node is a TreeViewLabel)
            self.selected_results = new_node.data if hasattr(new_node, 'data') else None
        else:
            print("Root result item selected (?)")

    def on_change_selected_results_dir(self, instance, new_results_dir):
        self.refresh_tree(new_results_dir)

        #Select the first selectable node
        if self.treeview is not None:
            if len(self.treeview.get_root().nodes) > 0:
                self.treeview.select_node(self.treeview.get_root().nodes[0])


class ResultDetailSelectorWidget(BorderedBoxLayout):
    results_selector_widget = ObjectProperty(None, allownone=True)
    estimate_name = StringProperty(None, allownone=True)
    model_name = StringProperty(None, allownone=True)

    # allows selection of model, gaugeopt, etc.
    def __init__(self, **kwargs):
        kwargs['orientation'] = 'vertical'
        kwargs['size_hint_y'] = None
        super().__init__(**kwargs)
        self.results = None  # the current results object
        self.rows = []  # list of horizontal BoxLayouts, one per row

    def on_results_selector_widget(self, inst, val):
        if self.results_selector_widget is None: return
        if self.results_selector_widget.selected_results is not None:
            self.on_change_selected_results(None, self.results_selector_widget.selected_results)
        self.results_selector_widget.bind(selected_results=self.on_change_selected_results)

    def rebuild(self):
        self.clear_widgets()
        for row in self.rows:
            self.add_widget(row)
        self.height = len(self.rows) * 60

    def on_change_selected_results(self, instance, new_results_obj):
        self.rows.clear()
        self.results = new_results_obj  # make this the current results object

        if isinstance(new_results_obj, _ModelEstimateResults):
            estimate_row = BoxLayout(orientation='horizontal')
            estimate_keys = list(new_results_obj.estimates.keys())
            estimate_spinner = Spinner(text=estimate_keys[0] if (len(estimate_keys) > 0) else '(none)',
                                       values=estimate_keys, size_hint=(0.6, 1.0))
            estimate_spinner.bind(text=self.on_change_selected_estimate)
            estimate_row.add_widget(Label(text='Estimate:', size_hint=(0.4, 1.0)))
            estimate_row.add_widget(estimate_spinner)
            self.rows.append(estimate_row)
            self.on_change_selected_estimate(None, estimate_keys[0] if (len(estimate_keys) > 0) else '(none)')
        else:
            self.rows.append(Label(text='No details'))
            self.rebuild()

    def on_change_selected_estimate(self, spinner, new_estimate_key):
        #Note: this is only called when self.results is a ModelEstimateResults object
        if len(self.rows) > 1:
            self.remove_widget(self.rows[1])  # remove second row == "Model: ..." row

        self.estimate_name = new_estimate_key
        if new_estimate_key is not None:
            estimate = self.results.estimates[new_estimate_key]
            model_names = list(estimate.models.keys())
        else:
            model_names = []

        model_row = BoxLayout(orientation='horizontal')
        model_spinner = Spinner(text=model_names[0] if (len(model_names) > 0) else '(none)',
                                values=model_names, size_hint=(0.6, 1.0))
        model_spinner.bind(text=self.on_change_selected_model)
        model_row.add_widget(Label(text='Model:', size_hint=(0.4, 1.0)))
        model_row.add_widget(model_spinner)
        self.rows.append(model_row)
        self.rebuild()
        self.on_change_selected_model(None, model_names[0] if (len(model_names) > 0) else None)

    def on_change_selected_model(self, spinner, new_model_name):
        self.model_name = new_model_name  # set property so other layouts can trigger off of?


class CenterAreaWidget(BoxLayout, StencilView):
    # needs menus of all available tables/plots to add (for currently selected results/model/data/gaugeopt, etc)
    resultsdir_selector_widget = ObjectProperty(None, allownone=True)
    results_selector_widget = ObjectProperty(None, allownone=True)
    resultdetail_selector_widget = ObjectProperty(None, allownone=True)

    def __init__(self, **kwargs):
        kwargs['orientation'] = 'vertical'
        super().__init__(**kwargs)
        self._sidebar_layout = self._status_label = None

    def on_results_selector_widget(self, inst, val):
        self.results_selector_widget.bind(selected_results=self.selection_change)

    def on_resultdetail_selector_widget(self, inst, val):
        self.resultdetail_selector_widget.bind(estimate_name=self.selection_change, model_name=self.selection_change)

    def selection_change(self, instance, value):
        print("Data area noticed a selected results or model change... do something in the future?")

    def set_info_containers(self, sidebar_layout, status_label):
        self._sidebar_layout = sidebar_layout
        self._status_label = status_label

    def add_item(self, item_text):
        print("Adding item ", item_text)

        resultsdir = self.resultsdir_selector_widget.selected_results_dir
        data = resultsdir.data
        edesign = data.edesign

        results = self.results_selector_widget.selected_results

        circuit_list = edesign.all_circuits_needing_data
        dataset = data.dataset

        if isinstance(edesign, _StandardGSTDesign):
            max_length_list = edesign.maxlengths
            circuits_by_L = edesign.circuit_lists
        else:
            max_length_list = None
            circuits_by_L = None

        if isinstance(results, _ModelEstimateResults):
            estimate = results.estimates[self.resultdetail_selector_widget.estimate_name]
            model = estimate.models[self.resultdetail_selector_widget.model_name]
            target_model = estimate.models['target'] if 'target' in estimate.models else Nonee
            models = [model]
            titles = ['Estimate']
            objfn_builder = estimate.parameters.get(
                'final_objfn_builder', _objfns.ObjectiveFunctionBuilder.create_from('logl'))
            models_by_L = [estimate.models['iteration %d estimate' % i] for i in range(len(max_length_list))] \
                if (max_length_list is not None) else None
            est_lbls_mt = [est_name for est_name in results.estimates if est_name != "Target"]
            est_mdls_mt = [results.estimates[est_name].models.get('final iteration estimate', None)
                           for est_name in est_lbls_mt]
            gaugeopt_args = estimate.goparameters.get(self.resultdetail_selector_widget.model_name, {})
            estimate_params = estimate.parameters
        else:
            estimate = model = target_model = None
            models = titles = []
            objfn_builder = None
            models_by_L = None
            est_lbls_mt = None
            est_mdls_mt = None
            gaugeopt_args = {}
            estimate_params = {}
        cri = None

        ws = Workspace(gui_mode='kivy')
        wstable = None
        wsplot = None
        if item_text == 'SpamTable':
            wstable = ws.SpamTable(models, titles, 'boxes', cri, False)  # titles?
        elif item_text == 'SpamParametersTable':
            wstable = ws.SpamParametersTable(models, titles, cri)
        elif item_text == 'GatesTable':
            wstable = ws.GatesTable(models, titles, 'boxes', cri)
        elif item_text == 'ChoiTable':
            wstable = ws.ChoiTable(models, titles, cri)
        elif item_text == 'ModelVsTargetTable':
            clifford_compilation = None
            wstable = ws.ModelVsTargetTable(model, target_model, clifford_compilation, cri)
        elif item_text == 'GatesVsTargetTable':
            wstable = ws.GatesVsTargetTable(model, target_model, cri)  # wildcard?
        elif item_text == 'SpamVsTargetTable':
            wstable = ws.SpamVsTargetTable(model, target_model, cri)
        elif item_text == 'ErrgenTable':
            wstable = ws.ErrgenTable(model, target_model, cri)  # (more options)
        elif item_text == 'NQubitErrgenTable':
            wstable = ws.NQubitErrgenTable(model, cri)
        elif item_text == 'GateDecompTable':
            wstable = ws.GateDecompTable(model, target_model, cri)
        elif item_text == 'GateEigenvalueTable':
            wstable = ws.GateEigenvalueTable(model, target_model, cri,
                                             display=('evals', 'rel', 'log-evals', 'log-rel'))
        elif item_text == 'DataSetOverviewTable':
            wstable = ws.DataSetOverviewTable(dataset, max_length_list)
        elif item_text == 'SoftwareEnvTable':
            wstable = ws.SoftwareEnvTable()
        elif item_text == 'CircuitTable':
            # wstable = ws.CircuitTable(...)  # wait until we can select circuit list; e.g. germs, fiducials
            print("Wait until better selection methods to create circuit tables...")
        elif item_text == 'GatesSingleMetricTable':
            #metric = 'inf'  # entanglement infidelity
            #wstable = GatesSingleMetricTable(metric, ...)
            print("Wait until better selection methods to create single-item gate metric tables...")
        elif item_text == 'StandardErrgenTable':
            wstable = ws.StandardErrgenTable(model.dim, 'hamiltonian', 'pp')  # not super useful; what about 'stochastic'?
        elif item_text == 'GaugeOptParamsTable':
            wstable = ws.GaugeOptParamsTable(gaugeopt_args)
        elif item_text == 'MetadataTable':
            wstable = ws.MetadataTable(model, estimate_params)
        elif item_text == 'WildcardBudgetTable':
            wstable = ws.WildcardBudgetTable(estimate_params.get("unmodeled_error", None))
        elif item_text == 'FitComparisonTable':
            wstable = ws.FitComparisonTable(max_length_list, circuits_by_L, models_by_L, dataset)
        elif item_text == 'FitComparisonBarPlot':
            wsplot = ws.FitComparisonBarPlot(max_length_list, circuits_by_L, models_by_L, dataset)
        elif item_text == 'FitComparisonBarPlotB':
            wsplot = ws.FitComparisonBarPlot(est_lbls_mt, [circuit_list] * len(est_mdls_mt),
                                             est_mdls_mt, [dataset] * len(est_mdls_mt), objfn_builder)

        elif item_text == 'FitComparisonBoxPlot':
            # used for multiple data sets -- enable this once we get better selection methods
            print("Wait until better selection methods to create fit comparison box plot...")
        elif item_text in ('ColorBoxPlot', 'ColorScatterPlot', 'ColorHistogramPlot'):

            if item_text == 'ColorBoxPlot': plot_type = "boxes"
            elif item_text == "ColorScatterPlot": plot_type = "scatter"
            else: plot_type = "histogram"

            linlog_percentile = 5
            bgcolor = 'white'
            wsplot = ws.ColorBoxPlot(
                objfn_builder, circuit_list,
                dataset, model,  # could use non-gauge-opt model here?
                linlg_pcntle=linlog_percentile / 100, comm=None, bgcolor=bgcolor,
                typ=plot_type)

        else:
            wstable = wsplot = None

        if wstable is not None:
            tbl = wstable.tables[0]
            out = tbl.render('kivywidget', kivywidget_kwargs={'size_hint': (None, None)})
            tblwidget = out['kivywidget']
            #self.data_area.clear_widgets()
            fig = FigureContainer(tblwidget, item_text, size_hint=(None, None))
            set_info_containers(fig, self._sidebar_layout, self._status_label)
            self.data_area.add_widget(fig)
        elif wsplot is not None:
            plt = wsplot.figs[0]
            constructor_fn, kwargs = plt.kivywidget
            natural_size = plt.metadata.get('natural_size', (300, 300))
            kwargs.update({'size_hint': (None, None)})
            pltwidget = constructor_fn(**kwargs)
            pltwidget.size = natural_size
            print("DB: PLOT Initial size = ", natural_size)
            #self.data_area.clear_widgets()
            fig = FigureContainer(pltwidget, item_text, size_hint=(None, None))
            set_info_containers(fig, self._sidebar_layout, self._status_label)
            self.data_area.add_widget(fig)
        else:
            print("Cannot create " + item_text + " yet.")


class DataAreaWidget(RelativeLayout):

    active_child_widget = ObjectProperty(None, allownone=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        with self.canvas:
            Color(0.7, 0.7, 0.7, 1)
            self._bgrect = Rectangle(pos=(0,0), size=self.size)  # note: *relative* coords
        self.bind(size=self._draw)

        #Bind to keyboard events
        self._keyboard = Window.request_keyboard(self._keyboard_closed, self, 'text')
        self._keyboard.bind(on_key_down=self._on_keyboard_down)

    def _keyboard_closed(self):
        self._keyboard.unbind(on_key_down=self._on_keyboard_down)
        self._keyboard = None

    def _on_keyboard_down(self, keyboard, keycode, text, modifiers):
        #print('The key', keycode, 'have been pressed')
        #print(' - text is %r' % text)
        #print(' - modifiers are %r' % modifiers)

        if keycode[1] == 'backspace' and self.active_child_widget is not None:
            self.remove_widget(self.active_child_widget)
            self.active_child_widget = None
            return True

        # Return True to accept the key. Otherwise, it will be used by the system.
        return False

    def _draw(self, *args):
        self._bgrect.size = self.size

    def set_active(self, active_child_widget):
        if self.active_child_widget is not None:
            self.active_child_widget.deactivate()

        if active_child_widget is not None:
            active_child_widget.activate()
        self.active_child_widget = active_child_widget


class FigureContainer(DragBehavior, ResizableBehavior, BoxLayout):
    def __init__(self, fig_widget, title, **kwargs):
        kwargs['orientation'] = 'vertical'
        resize_kwargs = dict(
            resizable_left=False,
            resizable_right=True,
            resizable_up=False,
            resizable_down=True,
            resizable_border=10,
            resizable_border_offset=5)
        ResizableBehavior.__init__(self, **resize_kwargs)
        BoxLayout.__init__(self, **kwargs)
        initial_size = fig_widget.size

        with self.canvas.before:
            Color(0.0, 0.4, 0.4, 0.0)  # Make opaque when debugging - turquoise figure background
            self.bgrect = Rectangle(pos=self.pos, size=self.size)

            self.active_color = Color(0.8, 0.8, 0, 0)  # select / active box color
            self.active_border_thickness = 4
            x1, y1 = self.x, self.y
            x2, y2 = self.x + self.width, self.y + self.height
            pts = [x1, y1, x2, y1, x2, y2, x1, y2, x1, y1]
            self.activebox = Line(points=pts, width=self.active_border_thickness)

        self.size = initial_size

        fig_widget.size_hint_x = 1.0
        fig_widget.size_hint_y = 1.0
        self.title = title
        self.content = fig_widget
        self.add_widget(Label(text=title, bold=True, size_hint_y=None, height=50, color=(0,0,0,1), font_size=18))
        self.add_widget(fig_widget)
        #self.set_cursor_mode(0)

        db = self.drag_border = 2 * (self.resizable_border - self.resizable_border_offset)  # 2 for extra measure
        drag_kwargs = {'drag_rectangle': (self.x + db, self.y + db, self.width - 2 * db, self.height - 2 * db),
                       'drag_timeout': 2000 }  # wait 2 seconds before giving up on drag
        DragBehavior.__init__(self, **drag_kwargs)
        print("initial drag rect = ", self.drag_rectangle)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            print("Figure %s received touch-down event" % self.title)
            if self.parent is not None:
                if self.parent.active_child_widget is self:
                    self.parent.set_active(None)
                else:
                    self.parent.set_active(self)
            # don't count activation as actual 'processing', so continue on and
            # let super decide whether this event is processed
            return super().on_touch_down(touch)

            #import bpdb; bpdb.set_trace()
            #touch.push()
            #touch.apply_transform_2d(self.to_window)
            ##tpos = self.to_window(*touch.pos)
            #ret = super().on_touch_down(touch)
            #touch.pop()
            #return ret
        else:
            return False

    def deactivate(self):
        #REMOVE print("deactivated ", self.title)
        self.active_color.a = 0.0

    def activate(self):
        #REMOVE print("activated ", self.title)
        self.active_color.a = 1.0

    def _redraw(self):
        self.bgrect.pos = self.pos
        self.bgrect.size = self.size

        x1, y1 = self.x, self.y
        x2, y2 = self.x + self.width, self.y + self.height
        self.activebox.points = [x1, y1, x2, y1, x2, y2, x1, y2, x1, y1]

    def on_size(self, *args):
        self._redraw()

    def on_pos(self, *args):
        self._redraw()
        #print("Pos change: ", self.pos, ' drag_rect = ',self.drag_rectangle)
        db = self.drag_border
        self.drag_rectangle = (self.pos[0] + db, self.pos[1] + db, self.size[0] - 2 * db, self.size[1] - 2 * db)


class CustomAccordionItem(AccordionItem):
    #Overrides _update_title so we don't have to use (deprecated) templates to customize them
    # Basically copied from accordion.py
    def _update_title(self, dt):
        if not self.container_title:
            self._trigger_title()
            return
        c = self.container_title
        c.clear_widgets()
        instance = CustomAccordionTitle(self.title, self, bold=True, font_size=24)
        c.add_widget(instance)


class CustomAccordionTitle(Label):
    """ Mimics the (deprecated) default Kivy template for an accordion title"""
    def __init__(self, text, item, **kwargs):
        from kivy.graphics import PushMatrix, PopMatrix, Translate, Rotate, BorderImage
        super().__init__(text=text, **kwargs)

        with self.canvas.before:
            Color(1, 1, 1, 1)
            self.bi = BorderImage(source=item.background_normal if item.collapse else item.background_selected,
                                  pos=self.pos, size=self.size)
            PushMatrix()
            self.t1 = Translate(xy=(self.center_x, self.center_y))
            Rotate(angle= 90 if item.orientation == 'horizontal' else 0, axis=(0, 0, 1))
            self.t2 = Translate(xy=(-self.center_x, -self.center_y))

        with self.canvas.after:
            PopMatrix

        self.bind(pos=self.update, size=self.update)
        item.bind(collapse=lambda inst, v: setattr(self.bi, 'source', inst.background_normal
                                                   if v else inst.background_selected))

    def update(self, *args):
        self.bi.pos = self.pos
        self.bi.size = self.size
        self.t1.xy = (self.center_x, self.center_y)
        self.t2.xy = (-self.center_x, -self.center_y)


def add_row(widget, k, v):
    row = BoxLayout(orientation='horizontal')
    row.add_widget(Label(text=str(k), font_size=20))
    row.add_widget(Label(text=str(v), font_size=20))
    widget.add_widget(row)


class ProcessorSpecModal(ModalView):
    def __init__(self, results_dir, **kwargs):
        super().__init__(**kwargs)

        layout = BoxLayout(orientation='vertical')

        from pygsti.protocols.gst import HasProcessorSpec as _HasProcessorSpec
        #root_edesign = self.results_dir_selector_widget.root_results_dir.data.edesign
        edesign = results_dir.data.edesign
        pspec = edesign.processor_spec if isinstance(edesign, _HasProcessorSpec) else None
        layout.add_widget(Label(text='Processor Specification', font_size=24, bold=True))

        if pspec is not None:
            add_row(layout, '# of qubits:', pspec.num_qubits)
            add_row(layout, 'gate names:', pspec.gate_names)  # clickable for availability?
            add_row(layout, 'prep names:', pspec.prep_names)
            add_row(layout, 'POVM names:', pspec.povm_names)
        else:
            layout.add_widget(Label(text='(no info)', font_size=18))
        self.add_widget(layout)


#class ProcessorSpecInfoWidget(BoxLayout):
#    def __init__(self, results_dir_selector_widget, **kwargs):
#        kwargs['orientation'] = 'vertical'
#        super().__init__(**kwargs)
#        results_dir_selector_widget.bind(selected_results_dir=self.update_selected)
#        self.results_dir_selector_widget = results_dir_selector_widget
#        self.update_selected(None, None)
#
#    def update_selected(self, inst, val):
#        from pygsti.protocols.gst import HasProcessorSpec as _HasProcessorSpec
#        #root_edesign = self.results_dir_selector_widget.root_results_dir.data.edesign
#        edesign = self.results_dir_selector_widget.selected_results_dir.data.edesign
#        pspec = edesign.processor_spec if isinstance(edesign, _HasProcessorSpec) else None
#        self.clear_widgets()
#        self.add_widget(Label(text='Processor Specification', font_size=24, bold=True))
#        
#        def add_row(k, v):
#            row = BoxLayout(orientation='horizontal')
#            row.add_widget(Label(text=str(k), font_size=20))
#            row.add_widget(Label(text=str(v), font_size=20))
#            self.add_widget(row)
#
#        if pspec is not None:
#            add_row('# of qubits:', pspec.num_qubits)
#            add_row('gate names:', pspec.gate_names)  # clickable for availability?
#            add_row('prep names:', pspec.prep_names)
#            add_row('POVM names:', pspec.povm_names)
#        else:
#            self.add_widget(Label(text='(no info)', font_size=18))


class ExperimentDesignModal(ModalView):
    def __init__(self, results_dir, **kwargs):
        super().__init__(**kwargs)
        
        layout = BoxLayout(orientation='vertical')

        from pygsti.protocols.protocol import CircuitListsDesign, CombinedExperimentDesign, \
            SimultaneousExperimentDesign
        from pygsti.protocols.gst import GateSetTomographyDesign, StandardGSTDesign
        edesign = results_dir.data.edesign

        layout.add_widget(Label(text='Experiment Design', font_size=24, bold=True))
        add_row(layout, 'Type:', str(edesign.__class__.__name__))
        add_row(layout, '# of circuits:', len(edesign.all_circuits_needing_data))
        if isinstance(edesign, CircuitListsDesign):
            add_row(layout, 'Circuit list lengths:', ", ".join(map(str, map(len, edesign.circuit_lists))))
        if isinstance(edesign, CombinedExperimentDesign):
            add_row(layout, 'Sub-designs:', ", ".join(map(str, edesign.keys())))
        if isinstance(edesign, SimultaneousExperimentDesign):
            add_row(layout, 'Sub-designs:', ", ".join(map(str, edesign.keys())))
        if isinstance(edesign,  GateSetTomographyDesign):
            pass  # doesn't add anything beyond a CircuitListsDesign other than a processor spec
        if isinstance(edesign, StandardGSTDesign):
            add_row(layout, '# prep fiducials:', len(edesign.prep_fiducials))
            add_row(layout, '# meas fiducials:', len(edesign.meas_fiducials))
            add_row(layout, '# germs:', len(edesign.germs))
            add_row(layout, 'Max-depths:', ", ".join(map(str, edesign.maxlengths)))
        self.add_widget(layout)


class DatasetModal(ModalView):
    def __init__(self, results_dir, **kwargs):
        super().__init__(**kwargs)
        layout = BoxLayout(orientation='vertical')

        ds = results_dir.data.dataset

        layout.add_widget(Label(text='DataSet', font_size=24, bold=True))
        add_row(layout, '# of circuits:', len(ds))
        if ds.has_constant_totalcounts_pertime:
            add_row(layout, 'samples per circuit:', ds.totalcounts_pertime)
        if not ds.has_trivial_timedependence:
            add_row(layout, 'timestamps:', ds.timestamps)
        self.add_widget(layout)

#REMOVE
#class TableWidget(GridLayout):
#    def __init__(self, num_rows, num_cols, **kwargs):
#        super(TableWidget, self).__init__(**kwargs)
#        self.rows = num_rows
#        self.cols = num_cols
#        self.padding = 2
#        self.spacing = 5
#
#        for i in range(self.rows):
#            for j in range(self.cols):
#                l = Label(text='%d,%d' % (i, j))
#                l.color = (0,0,0,1)  # black text
#                self.add_widget(l)
#
#        #self.username = TextInput(multiline=False)
#        #self.add_widget(self.username)
#        #self.add_widget(Label(text='password'))
#        #self.password = TextInput(password=True, multiline=False)
#        #self.add_widget(self.password)
#
#        #self.on_size()
#
#    def on_size(self, *args):
#        self.canvas.before.clear()
#        with self.canvas.before:
#            Color(0.5, 0.5, 0.5)  # table background
#            Rectangle(pos=self.pos, size=self.size)

from kivy.lang import Builder


class LoadDialog(FloatLayout):
    load = ObjectProperty(None)
    cancel = ObjectProperty(None)


load_dialog_kv = \
"""<LoadDialog>:
    BoxLayout:
        size: root.size
        pos: root.pos
        orientation: "vertical"
        FileChooserListView:
            id: filechooser

        BoxLayout:
            size_hint_y: None
            height: 60
            Button:
                text: "Cancel"
                on_release: root.cancel()

            Button:
                text: "Load"
                on_release: root.load(filechooser.path, filechooser.selection)
"""
Builder.load_string(load_dialog_kv)


class DataExplorerApp(App):
    def __init__(self, results_dir):  #, test_widget):
        self.results_dir = results_dir        
        #self.test_widget = test_widget
        super().__init__()

    def build(self):
        return RootExplorerWidget(self.results_dir)


#if __name__ == '__main__':
#    DataExplorerApp(tblwidget).run()
