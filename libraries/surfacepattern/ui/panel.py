#! python3
# Eto panel UI: LabeledSlider, sections, footer actions (talks only to the session).

import traceback

import Eto.Drawing
import Eto.Forms
import Rhino
import scriptcontext

from surfacepattern.core.session import get_session

PANEL_STICKY_KEY = "surfacepattern_panel"
DRAFT_INTERVAL = 0.07    # seconds; draft recompute while dragging
COMMIT_INTERVAL = 0.25   # seconds of inactivity before the full recompute
NOTICE_INTERVAL = 0.8    # seconds the clamp notice stays visible

SHAPE_OPTIONS = ["circle", "slot", "hex"]
GRID_TYPE_OPTIONS = ["square", "staggered", "triangular"]
PLACEMENT_OPTIONS = ["uv", "world"]

PARAM_DEFAULTS = {
    "pattern_mode": "grid",
    "placement_mode": "uv",
    "shape": "circle",
    "size": 4.0,
    "slot_ratio": 0.4,
    "spacing_x": 10.0,
    "spacing_y": 10.0,
    "grid_type": "square",
    "jitter_position": 0.0,
    "jitter_size": 0.0,
    "jitter_rotation": 0.0,
    "rotation": 0.0,
    "seed": 0,
}


def eto_handler(func):
    """Wrap an Eto event handler with try/except — Eto swallows handler exceptions silently."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            Rhino.RhinoApp.WriteLine("SurfacePattern UI error:\n" + traceback.format_exc())
    return wrapper


class LabeledSlider:
    """Label + slider + numeric field on one row, two-way synced.

    Double-click restores the default; out-of-range numeric input clamps to the nearest
    valid value with a brief background-color notice on the text field.
    """

    def __init__(self, label, minimum, maximum, default, step=1.0, on_change=None):
        self.minimum = minimum
        self.maximum = maximum
        self.default = default
        self.on_change = on_change
        self._scale = 1.0 / step
        self._updating = False

        self.label = Eto.Forms.Label()
        self.label.Text = label

        self.slider = Eto.Forms.Slider()
        self.slider.MinValue = int(round(minimum * self._scale))
        self.slider.MaxValue = int(round(maximum * self._scale))
        self.slider.Value = int(round(default * self._scale))
        self.slider.Width = 130
        self.slider.ValueChanged += eto_handler(self._slider_changed)
        self.slider.MouseDoubleClick += eto_handler(self._restore_default)

        self.textbox = Eto.Forms.TextBox()
        self.textbox.Width = 52
        self.textbox.Text = self._format(default)
        self._normal_background = self.textbox.BackgroundColor
        self.textbox.KeyDown += eto_handler(self._textbox_keydown)
        self.textbox.LostFocus += eto_handler(self._textbox_commit)
        self.textbox.MouseDoubleClick += eto_handler(self._restore_default)

        self._notice_timer = Eto.Forms.UITimer()
        self._notice_timer.Interval = NOTICE_INTERVAL
        self._notice_timer.Elapsed += eto_handler(self._clear_notice)

    @property
    def value(self):
        return self.slider.Value / self._scale

    def _format(self, number):
        return "{:g}".format(round(number, 3))

    def _set_value(self, number, commit):
        clamped = min(max(number, self.minimum), self.maximum)
        if clamped != number:
            self._show_clamp_notice()
        self._updating = True
        self.slider.Value = int(round(clamped * self._scale))
        self.textbox.Text = self._format(self.value)
        self._updating = False
        if self.on_change is not None:
            self.on_change(self.value, commit)

    def _slider_changed(self, _sender, _event):
        if self._updating:
            return
        self._updating = True
        self.textbox.Text = self._format(self.value)
        self._updating = False
        if self.on_change is not None:
            self.on_change(self.value, False)

    def _restore_default(self, _sender, _event):
        self._set_value(self.default, True)

    def _textbox_keydown(self, _sender, event):
        if event.Key == Eto.Forms.Keys.Enter:
            self._textbox_commit(None, None)
            event.Handled = True

    def _textbox_commit(self, _sender, _event):
        if self._updating:
            return
        try:
            number = float(self.textbox.Text)
        except (TypeError, ValueError):
            self._show_clamp_notice()
            self._updating = True
            self.textbox.Text = self._format(self.value)
            self._updating = False
            return
        self._set_value(number, True)

    def _show_clamp_notice(self):
        self.textbox.BackgroundColor = Eto.Drawing.Colors.LightYellow
        self._notice_timer.Stop()
        self._notice_timer.Start()

    def _clear_notice(self, _sender, _event):
        self._notice_timer.Stop()
        self.textbox.BackgroundColor = self._normal_background

    def row(self):
        return [self.label, self.slider, self.textbox]


class SurfacePatternPanel(Eto.Forms.Form):
    """Modeless panel driving the session; sliders never mutate the document."""

    def __init__(self):
        super().__init__()
        self.Title = "SurfacePattern"
        self.Padding = Eto.Drawing.Padding(8)
        self.Resizable = False
        self.Maximizable = False
        self.Minimizable = False

        session = get_session()
        for key, value in PARAM_DEFAULTS.items():
            session.params.setdefault(key, value)

        self._draft_dirty = False
        self._draft_timer = Eto.Forms.UITimer()
        self._draft_timer.Interval = DRAFT_INTERVAL
        self._draft_timer.Elapsed += eto_handler(self._draft_tick)
        self._commit_timer = Eto.Forms.UITimer()
        self._commit_timer.Interval = COMMIT_INTERVAL
        self._commit_timer.Elapsed += eto_handler(self._commit_tick)

        self.Content = self._build_layout(session)
        self.Closed += eto_handler(self._on_closed)

    # ---- layout -------------------------------------------------------------

    def _build_layout(self, session):
        layout = Eto.Forms.DynamicLayout()
        layout.Spacing = Eto.Drawing.Size(6, 6)

        # Target section.
        self.pick_button = Eto.Forms.Button()
        self.pick_button.Text = "Pick Targets"
        self.pick_button.Click += eto_handler(self._pick_targets)
        self.target_label = Eto.Forms.Label()
        self.target_label.Text = self._target_summary(session)
        self.placement_dropdown = self._dropdown(
            PLACEMENT_OPTIONS, session.params.get("placement_mode", "uv"), "placement_mode"
        )
        layout.AddRow(self.pick_button, self.target_label, None)
        layout.AddRow(Eto.Forms.Label(Text="Placement"), self.placement_dropdown, None)

        # Grid section, most-used first: shape, size, spacing, grid type, jitter, rotation, seed.
        self.shape_dropdown = self._dropdown(
            SHAPE_OPTIONS, session.params.get("shape", "circle"), "shape"
        )
        self.grid_type_dropdown = self._dropdown(
            GRID_TYPE_OPTIONS, session.params.get("grid_type", "square"), "grid_type"
        )
        self.sliders = {}
        grid = Eto.Forms.DynamicLayout()
        grid.Spacing = Eto.Drawing.Size(6, 4)
        grid.AddRow(Eto.Forms.Label(Text="Shape"), self.shape_dropdown, None)
        grid.AddRow(*self._slider("Size (mm)", "size", 0.5, 50.0, 0.1))
        grid.AddRow(*self._slider("Slot Ratio", "slot_ratio", 0.1, 1.0, 0.05))
        grid.AddRow(*self._slider("Spacing X (mm)", "spacing_x", 1.0, 100.0, 0.5))
        grid.AddRow(*self._slider("Spacing Y (mm)", "spacing_y", 1.0, 100.0, 0.5))
        grid.AddRow(Eto.Forms.Label(Text="Grid Type"), self.grid_type_dropdown, None)
        grid.AddRow(*self._slider("Jitter Pos %", "jitter_position", 0.0, 100.0, 1.0))
        grid.AddRow(*self._slider("Jitter Size %", "jitter_size", 0.0, 100.0, 1.0))
        grid.AddRow(*self._slider("Jitter Rot %", "jitter_rotation", 0.0, 100.0, 1.0))
        grid.AddRow(*self._slider("Rotation (deg)", "rotation", 0.0, 360.0, 1.0))
        grid.AddRow(*self._slider("Seed", "seed", 0.0, 9999.0, 1.0))

        group = Eto.Forms.GroupBox()
        group.Text = "Grid"
        group.Content = grid
        layout.AddRow(group)
        layout.Add(None)
        return layout

    def _slider(self, label, key, minimum, maximum, step):
        session = get_session()
        default = PARAM_DEFAULTS.get(key, minimum)
        control = LabeledSlider(
            label,
            minimum,
            maximum,
            session.params.get(key, default),
            step,
            on_change=lambda value, commit, key=key: self._param_changed(key, value, commit),
        )
        control.default = default
        self.sliders[key] = control
        return control.row()

    def _dropdown(self, options, current, key):
        dropdown = Eto.Forms.DropDown()
        for option in options:
            dropdown.Items.Add(option)
        dropdown.SelectedIndex = options.index(current) if current in options else 0
        dropdown.SelectedIndexChanged += eto_handler(
            lambda sender, _event, key=key, options=options: self._param_changed(
                key, options[sender.SelectedIndex], True
            )
        )
        return dropdown

    # ---- session wiring -----------------------------------------------------

    def _target_summary(self, session):
        if not session.targets:
            return "No targets — pick a surface"
        return "{} face(s)".format(len(session.targets))

    def _param_changed(self, key, value, commit):
        session = get_session()
        if key == "seed":
            value = int(value)
        session.params[key] = value
        if not session.targets:
            return
        if commit:
            self._draft_timer.Stop()
            self._commit_timer.Stop()
            self._draft_dirty = False
            session.request_recompute(False)
        else:
            self._draft_dirty = True
            self._draft_timer.Start()
            self._commit_timer.Stop()
            self._commit_timer.Start()

    def _draft_tick(self, _sender, _event):
        if self._draft_dirty:
            self._draft_dirty = False
            get_session().request_recompute(True)
        else:
            self._draft_timer.Stop()

    def _commit_tick(self, _sender, _event):
        self._commit_timer.Stop()
        self._draft_timer.Stop()
        self._draft_dirty = False
        get_session().request_recompute(False)

    def _pick_targets(self, _sender, _event):
        from surfacepattern.core.session import pick_targets

        session = get_session()
        self.Enabled = False  # lock panel input during viewport picking
        try:
            picked = pick_targets(session)
        finally:
            self.Enabled = True
        self.target_label.Text = self._target_summary(session)
        if picked:
            mode = session.params.get("placement_mode", "uv")
            if mode in PLACEMENT_OPTIONS:
                self.placement_dropdown.SelectedIndex = PLACEMENT_OPTIONS.index(mode)
            session.request_recompute(False)

    def _on_closed(self, _sender, _event):
        self._draft_timer.Stop()
        self._commit_timer.Stop()
        scriptcontext.sticky[PANEL_STICKY_KEY] = None


def show_panel():
    """Open (or focus) the singleton SurfacePattern panel, parented to the Rhino window."""
    existing = scriptcontext.sticky.get(PANEL_STICKY_KEY)
    if existing is not None and type(existing).__name__ == "SurfacePatternPanel":
        existing.BringToFront()
        return existing

    panel = SurfacePatternPanel()
    for_document = getattr(Rhino.UI.RhinoEtoApp, "MainWindowForDocument", None)
    panel.Owner = (
        for_document(scriptcontext.doc) if for_document else Rhino.UI.RhinoEtoApp.MainWindow
    )
    panel.Show()
    scriptcontext.sticky[PANEL_STICKY_KEY] = panel
    return panel
