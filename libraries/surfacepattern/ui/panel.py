#! python3
# Eto panel UI: LabeledSlider, sections, footer actions (talks only to the session).
#
# Implementation note: a dockable Rhino.UI.Panels panel requires registering a .NET
# panel type under a plugin GUID, which a CPython ScriptEditor project cannot do
# reliably, so this is a modeless Eto.Forms.Form instead, parented to the Rhino main
# window (Rhino.UI.RhinoEtoApp, per-document overload when available) so it never
# falls behind Rhino.

import Eto.Drawing
import Eto.Forms
import Rhino
import scriptcontext

from surfacepattern.core import errors
from surfacepattern.core.session import get_session

PANEL_STICKY_KEY = "surfacepattern_panel"
DRAFT_INTERVAL = 0.07    # seconds; draft recompute cadence while dragging
COMMIT_INTERVAL = 0.25   # seconds of inactivity before the full recompute
NOTICE_INTERVAL = 0.8    # seconds the clamp notice stays visible

PATTERN_MODES = ["grid", "halftone", "stamp"]
SHAPE_OPTIONS = ["circle", "slot", "hex"]
GRID_TYPE_OPTIONS = ["square", "staggered", "triangular"]
PLACEMENT_OPTIONS = ["uv", "world"]
HALFTONE_PROFILES = ["linear", "smooth", "gaussian"]
STAMP_PLACE_MODES = ["array", "click", "freehand"]
STAMP_PLACE_LABELS = ["Array", "Click Place", "Freehand"]
STAMP_SELECT_OPTIONS = ["cycle", "random"]

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
    "halftone_radius": 50.0,
    "halftone_profile": "linear",
    "halftone_invert": False,
    "halftone_size_min": 1.0,
    "halftone_size_max": 6.0,
    "halftone_cull": 0.0,
    "stamp_size": 10.0,
    "stamp_rotation": 0.0,
    "stamp_jitter": 0.0,
    "stamp_select": "cycle",
    "stamp_place_mode": "array",
}


def make_label(text):
    """Eto Label helper — pythonnet has no Label(Text=...) constructor-property syntax."""
    label = Eto.Forms.Label()
    label.Text = text
    return label


def add_list_item(collection, text):
    """Add a text item to an Eto item collection — pythonnet may not apply the implicit
    string-to-ListItem conversion that Items.Add("text") relies on."""
    item = Eto.Forms.ListItem()
    item.Text = text
    collection.Add(item)


def eto_handler(func):
    """Wrap an Eto event handler with try/except — Eto swallows handler exceptions silently.

    Full tracebacks go to the shared log file (one-line command-line notice with the
    path); the open panel, if any, shows a brief red error label.
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            errors.log_error("ui:" + func.__name__)
            panel = scriptcontext.sticky.get(PANEL_STICKY_KEY)
            if panel is not None and hasattr(panel, "show_error"):
                try:
                    panel.show_error()
                except Exception:
                    pass
    return wrapper


class LabeledSlider:
    """Label + Slider + numeric field on one row, two-way synced.

    Slider drags request draft recomputes; mouse-up, 250ms of inactivity (owner timer),
    or committed numeric entry request full recomputes. Double-click restores the
    default; out-of-range input clamps to the nearest valid value with a brief
    background-color notice.
    """

    def __init__(self, label, minimum, maximum, step, default, unit="", on_change=None):
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
        self.slider.Width = 120
        self.slider.ValueChanged += eto_handler(self._slider_changed)
        self.slider.MouseUp += eto_handler(self._slider_released)
        self.slider.MouseDoubleClick += eto_handler(self._restore_default)

        self.textbox = Eto.Forms.TextBox()
        self.textbox.Width = 48
        self.textbox.Text = self._format(default)
        self._normal_background = self.textbox.BackgroundColor
        self.textbox.KeyDown += eto_handler(self._textbox_keydown)
        self.textbox.LostFocus += eto_handler(self._textbox_commit)
        self.textbox.MouseDoubleClick += eto_handler(self._restore_default)

        self.unit_label = Eto.Forms.Label()
        self.unit_label.Text = unit

        self._notice_timer = Eto.Forms.UITimer()
        self._notice_timer.Interval = NOTICE_INTERVAL
        self._notice_timer.Elapsed += eto_handler(self._clear_notice)

    @property
    def value(self):
        return self.slider.Value / self._scale

    def row(self):
        field = Eto.Forms.StackLayout()
        field.Orientation = Eto.Forms.Orientation.Horizontal
        field.Spacing = 2
        field.Items.Add(Eto.Forms.StackLayoutItem(self.textbox))
        field.Items.Add(Eto.Forms.StackLayoutItem(self.unit_label))
        return [self.label, self.slider, field]

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

    def _slider_released(self, _sender, _event):
        # Mouse-up ends the drag: commit a full recompute at the final value.
        if self.on_change is not None:
            self.on_change(self.value, True)

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


class SurfacePatternPanel(Eto.Forms.Form):
    """Modeless panel driving the session; only the Bake button may mutate the document."""

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
        self._apply_mode_visibility(session.params.get("pattern_mode", "grid"))
        self.Shown += eto_handler(self._on_shown)
        self.Closed += eto_handler(self._on_closed)

    # ---- layout -------------------------------------------------------------

    def _build_layout(self, session):
        layout = Eto.Forms.DynamicLayout()
        layout.Spacing = Eto.Drawing.Size(6, 6)

        # (1) Target section: pick button, summary label, placement-mode dropdown.
        self.pick_button = Eto.Forms.Button()
        self.pick_button.Text = "Pick Targets"
        self.pick_button.Click += eto_handler(self._pick_targets)
        self.target_label = Eto.Forms.Label()
        self.target_label.Text = self._target_summary(session)
        self.placement_dropdown = self._dropdown(
            PLACEMENT_OPTIONS, session.params.get("placement_mode", "uv"), "placement_mode"
        )
        layout.AddRow(self.pick_button, self.target_label, None)
        layout.AddRow(make_label("Placement"), self.placement_dropdown, None)

        # (2) Pattern-mode segment: grid / halftone / stamp.
        self.mode_segment = Eto.Forms.RadioButtonList()
        self.mode_segment.Orientation = Eto.Forms.Orientation.Horizontal
        self.mode_segment.Spacing = Eto.Drawing.Size(8, 0)
        for mode in PATTERN_MODES:
            add_list_item(self.mode_segment.Items, mode.capitalize())
        current_mode = session.params.get("pattern_mode", "grid")
        self.mode_segment.SelectedIndex = (
            PATTERN_MODES.index(current_mode) if current_mode in PATTERN_MODES else 0
        )
        self.mode_segment.SelectedIndexChanged += eto_handler(self._mode_changed)
        layout.AddRow(self.mode_segment, None)

        # (3) Shared layout section (lattice parameters used by grid AND halftone),
        # then per-mode parameter sections, all collapsible.
        self.sliders = {}
        self.layout_section = self._layout_section(session)
        layout.AddRow(self.layout_section)
        self.mode_sections = {
            "grid": self._grid_section(session),
            "halftone": self._halftone_section(session),
            "stamp": self._stamp_section(session),
        }
        for mode in PATTERN_MODES:
            layout.AddRow(self.mode_sections[mode])

        # (4) Preview toggle.
        self.preview_checkbox = Eto.Forms.CheckBox()
        self.preview_checkbox.Text = "Preview"
        self.preview_checkbox.Checked = True
        self.preview_checkbox.CheckedChanged += eto_handler(self._preview_toggled)
        layout.AddRow(self.preview_checkbox, None)

        # Error status: brief red notice; details live in the log file.
        self.error_label = Eto.Forms.Label()
        self.error_label.Text = ""
        self.error_label.TextColor = Eto.Drawing.Colors.Red
        layout.AddRow(self.error_label, None)

        # (5) Fixed footer: preset dropdown + save (placeholders), bake button.
        # Destructive actions run only from these explicit clicks.
        self.preset_dropdown = Eto.Forms.DropDown()
        add_list_item(self.preset_dropdown.Items, "(default)")
        self.preset_dropdown.SelectedIndex = 0
        self.save_preset_button = Eto.Forms.Button()
        self.save_preset_button.Text = "Save"
        self.save_preset_button.Click += eto_handler(self._save_preset)
        self.bake_button = Eto.Forms.Button()
        self.bake_button.Text = "Bake"
        self.bake_button.Click += eto_handler(self._bake)
        layout.AddRow(self.preset_dropdown, self.save_preset_button, self.bake_button)
        return layout

    def _layout_section(self, session):
        # Lattice parameters shared by the grid AND halftone engines (halftone builds
        # on the grid lattice), so they stay visible and editable in both modes.
        self.shape_dropdown = self._dropdown(
            SHAPE_OPTIONS, session.params.get("shape", "circle"), "shape"
        )
        self.grid_type_dropdown = self._dropdown(
            GRID_TYPE_OPTIONS, session.params.get("grid_type", "square"), "grid_type"
        )
        content = Eto.Forms.DynamicLayout()
        content.Spacing = Eto.Drawing.Size(6, 4)
        content.AddRow(make_label("Shape"), self.shape_dropdown, None)
        content.AddRow(*self._slider("Slot Ratio", "slot_ratio", 0.1, 1.0, 0.05, ""))
        content.AddRow(*self._slider("Gap X", "spacing_x", 0.0, 100.0, 0.5, "mm"))
        content.AddRow(*self._slider("Gap Y", "spacing_y", 0.0, 100.0, 0.5, "mm"))
        content.AddRow(make_label("Grid Type"), self.grid_type_dropdown, None)
        content.AddRow(*self._slider("Jitter Pos", "jitter_position", 0.0, 100.0, 1.0, "%"))
        content.AddRow(*self._slider("Jitter Size", "jitter_size", 0.0, 100.0, 1.0, "%"))
        content.AddRow(*self._slider("Jitter Rot", "jitter_rotation", 0.0, 100.0, 1.0, "%"))
        content.AddRow(*self._slider("Rotation", "rotation", 0.0, 360.0, 1.0, "deg"))
        content.AddRow(*self._slider("Seed", "seed", 0.0, 9999.0, 1.0, ""))
        return self._expander("Layout", content)

    def _grid_section(self, session):
        grid = Eto.Forms.DynamicLayout()
        grid.Spacing = Eto.Drawing.Size(6, 4)
        grid.AddRow(*self._slider("Size", "size", 0.5, 50.0, 0.1, "mm"))
        return self._expander("Grid", grid)

    def _halftone_section(self, session):
        self.attractor_button = Eto.Forms.Button()
        self.attractor_button.Text = "Pick Attractors"
        self.attractor_button.Click += eto_handler(self._pick_attractors)
        self.attractor_label = Eto.Forms.Label()
        self.attractor_label.Text = self._attractor_summary(session)
        self.profile_dropdown = self._dropdown(
            HALFTONE_PROFILES, session.params.get("halftone_profile", "linear"), "halftone_profile"
        )
        self.invert_checkbox = Eto.Forms.CheckBox()
        self.invert_checkbox.Text = "Invert (small near attractors)"
        self.invert_checkbox.Checked = bool(session.params.get("halftone_invert", False))
        self.invert_checkbox.CheckedChanged += eto_handler(
            lambda _sender, _event: self._param_changed(
                "halftone_invert", bool(self.invert_checkbox.Checked), True
            )
        )

        halftone = Eto.Forms.DynamicLayout()
        halftone.Spacing = Eto.Drawing.Size(6, 4)
        halftone.AddRow(self.attractor_button, self.attractor_label, None)
        halftone.AddRow(*self._slider("Radius", "halftone_radius", 1.0, 500.0, 1.0, "mm"))
        halftone.AddRow(*self._slider("Size Min", "halftone_size_min", 0.0, 50.0, 0.1, "mm"))
        halftone.AddRow(*self._slider("Size Max", "halftone_size_max", 0.0, 50.0, 0.1, "mm"))
        halftone.AddRow(make_label("Profile"), self.profile_dropdown, None)
        halftone.AddRow(self.invert_checkbox, None)
        halftone.AddRow(*self._slider("Cull Below", "halftone_cull", 0.0, 20.0, 0.1, "mm"))
        return self._expander("Halftone", halftone)

    def _stamp_section(self, session):
        self.stamp_button = Eto.Forms.Button()
        self.stamp_button.Text = "Register Stamps"
        self.stamp_button.Click += eto_handler(self._register_stamps)
        self.stamp_label = Eto.Forms.Label()
        self.stamp_label.Text = self._stamp_summary(session)

        self.stamp_place_segment = Eto.Forms.RadioButtonList()
        self.stamp_place_segment.Orientation = Eto.Forms.Orientation.Horizontal
        self.stamp_place_segment.Spacing = Eto.Drawing.Size(8, 0)
        for label in STAMP_PLACE_LABELS:
            add_list_item(self.stamp_place_segment.Items, label)
        current = session.params.get("stamp_place_mode", "array")
        self.stamp_place_segment.SelectedIndex = (
            STAMP_PLACE_MODES.index(current) if current in STAMP_PLACE_MODES else 0
        )
        self.stamp_place_segment.SelectedIndexChanged += eto_handler(
            self._stamp_place_mode_changed
        )

        self.stamp_select_dropdown = self._dropdown(
            STAMP_SELECT_OPTIONS, session.params.get("stamp_select", "cycle"), "stamp_select"
        )

        self.stamp_action_button = Eto.Forms.Button()
        self.stamp_action_button.Click += eto_handler(self._stamp_action)

        self.stamp_clear_button = Eto.Forms.Button()
        self.stamp_clear_button.Text = "Clear Placed"
        self.stamp_clear_button.Click += eto_handler(self._clear_stamp_placements)
        self.stamp_placed_label = Eto.Forms.Label()
        self.stamp_placed_label.Text = self._stamp_placed_summary(session)

        content = Eto.Forms.DynamicLayout()
        content.Spacing = Eto.Drawing.Size(6, 4)
        content.AddRow(self.stamp_button, self.stamp_label, None)
        content.AddRow(self.stamp_place_segment, None)
        content.AddRow(make_label("Multi-Stamp"), self.stamp_select_dropdown, None)
        content.AddRow(*self._slider("Size", "stamp_size", 0.5, 200.0, 0.5, "mm"))
        content.AddRow(*self._slider("Rotation", "stamp_rotation", 0.0, 360.0, 1.0, "deg"))
        content.AddRow(*self._slider("Jitter", "stamp_jitter", 0.0, 100.0, 1.0, "%"))
        content.AddRow(self.stamp_action_button, None)
        content.AddRow(self.stamp_clear_button, self.stamp_placed_label, None)
        self._update_stamp_action()
        return self._expander("Stamp", content)

    def _expander(self, title, content):
        expander = Eto.Forms.Expander()
        expander.Header = make_label(title)
        expander.Expanded = True
        expander.Content = content
        return expander

    def _slider(self, label, key, minimum, maximum, step, unit):
        session = get_session()
        default = PARAM_DEFAULTS.get(key, minimum)
        control = LabeledSlider(
            label,
            minimum,
            maximum,
            step,
            session.params.get(key, default),
            unit,
            on_change=lambda value, commit, key=key: self._param_changed(key, value, commit),
        )
        control.default = default
        self.sliders[key] = control
        return control.row()

    def _dropdown(self, options, current, key):
        dropdown = Eto.Forms.DropDown()
        for option in options:
            add_list_item(dropdown.Items, option)
        dropdown.SelectedIndex = options.index(current) if current in options else 0
        dropdown.SelectedIndexChanged += eto_handler(
            lambda sender, _event, key=key, options=options: self._param_changed(
                key, options[sender.SelectedIndex], True
            )
        )
        return dropdown

    # ---- session wiring -----------------------------------------------------

    def show_error(self):
        """Show the brief red error notice (called by the handler decorator)."""
        self.error_label.Text = "Pattern error — see command line for log path"

    def clear_error(self):
        """Hide the error notice after a successful recompute."""
        self.error_label.Text = ""

    def _recompute(self, draft):
        """Recompute via the session; refresh labels and clear the error notice on success."""
        session = get_session()
        session.request_recompute(draft)
        # Recompute prunes dead targets/attractors/placements — keep the labels truthful.
        self.target_label.Text = self._target_summary(session)
        self.attractor_label.Text = self._attractor_summary(session)
        self.stamp_placed_label.Text = self._stamp_placed_summary(session)
        self.clear_error()

    def _attractor_summary(self, session):
        if not session.attractors:
            return "0 — face centers"
        return "{} attractor(s)".format(len(session.attractors))

    def _target_summary(self, session):
        if not session.targets:
            return "No targets — pick a surface"
        return "{} face(s)".format(len(session.targets))

    def _stamp_summary(self, session):
        if not session.stamps:
            return "0 — pick closed planar curves"
        return "{} stamp(s)".format(len(session.stamps))

    def _stamp_placed_summary(self, session):
        return "{} placed, {} stroke(s)".format(
            len(session.manual_placements), len(session.freehand_strokes)
        )

    def _update_stamp_action(self):
        place_mode = get_session().params.get("stamp_place_mode", "array")
        self.stamp_action_button.Visible = place_mode in ("click", "freehand")
        if place_mode == "click":
            self.stamp_action_button.Text = "Place Stamps (click in viewport)"
        elif place_mode == "freehand":
            self.stamp_action_button.Text = "Draw Stroke"

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
            self._recompute(False)
        else:
            self._draft_dirty = True
            self._draft_timer.Start()
            self._commit_timer.Stop()
            self._commit_timer.Start()

    def _draft_tick(self, _sender, _event):
        if self._draft_dirty:
            self._draft_dirty = False
            self._recompute(True)
        else:
            self._draft_timer.Stop()

    def _commit_tick(self, _sender, _event):
        self._commit_timer.Stop()
        self._draft_timer.Stop()
        self._draft_dirty = False
        self._recompute(False)

    def _mode_changed(self, sender, _event):
        mode = PATTERN_MODES[sender.SelectedIndex]
        session = get_session()
        session.params["pattern_mode"] = mode
        self._apply_mode_visibility(mode)
        if session.targets:
            self._recompute(False)

    def _apply_mode_visibility(self, mode):
        # The shared lattice section drives every lattice-based layout: grid,
        # halftone, and the stamp engine's array placement mode.
        place_mode = get_session().params.get("stamp_place_mode", "array")
        self.layout_section.Visible = mode in ("grid", "halftone") or (
            mode == "stamp" and place_mode == "array"
        )
        for name, section in self.mode_sections.items():
            section.Visible = name == mode

    def _preview_toggled(self, _sender, _event):
        get_session().set_preview_enabled(bool(self.preview_checkbox.Checked))

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
            self._recompute(False)

    def _pick_attractors(self, _sender, _event):
        from surfacepattern.core.session import pick_attractors

        session = get_session()
        self.Enabled = False  # lock panel input during viewport picking
        try:
            picked = pick_attractors(session)
        finally:
            self.Enabled = True
        self.attractor_label.Text = self._attractor_summary(session)
        if picked and session.targets:
            self._recompute(False)

    def _register_stamps(self, _sender, _event):
        from surfacepattern.core.session import pick_stamps

        session = get_session()
        self.Enabled = False  # lock panel input during viewport picking
        try:
            picked = pick_stamps(session)
        finally:
            self.Enabled = True
        self.stamp_label.Text = self._stamp_summary(session)
        if picked and session.targets:
            self._recompute(False)

    def _stamp_place_mode_changed(self, sender, _event):
        session = get_session()
        session.params["stamp_place_mode"] = STAMP_PLACE_MODES[sender.SelectedIndex]
        self._update_stamp_action()
        self._apply_mode_visibility(session.params.get("pattern_mode", "grid"))
        if session.targets:
            self._recompute(False)

    def _stamp_action(self, _sender, _event):
        from surfacepattern.core.session import stamp_click_place, stamp_draw_freehand

        session = get_session()
        place_mode = session.params.get("stamp_place_mode", "array")
        self.Enabled = False  # lock panel input during viewport interaction
        try:
            if place_mode == "click":
                stamp_click_place(session)
            elif place_mode == "freehand":
                stamp_draw_freehand(session)
        finally:
            self.Enabled = True
        # The interactive loops recompute themselves; just keep the labels truthful.
        self.stamp_placed_label.Text = self._stamp_placed_summary(session)
        self.target_label.Text = self._target_summary(session)

    def _clear_stamp_placements(self, _sender, _event):
        from surfacepattern.core.session import clear_stamp_placements

        session = get_session()
        clear_stamp_placements(session)
        self.stamp_placed_label.Text = self._stamp_placed_summary(session)

    def _save_preset(self, _sender, _event):
        Rhino.RhinoApp.WriteLine("SurfacePattern: preset save — next milestone.")

    def _bake(self, _sender, _event):
        Rhino.RhinoApp.WriteLine("SurfacePattern: bake — next milestone.")

    # ---- lifetime -----------------------------------------------------------

    def _on_shown(self, _sender, _event):
        get_session().set_preview_enabled(bool(self.preview_checkbox.Checked))

    def _on_closed(self, _sender, _event):
        self._draft_timer.Stop()
        self._commit_timer.Stop()
        get_session().set_preview_enabled(False)
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
