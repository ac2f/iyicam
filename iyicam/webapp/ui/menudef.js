/* The menus of the desktop program (docs/webapp/desktop_reference/ui_structure.json): every item with its shortcut.
 * `cmd` is the id of a command of the schema that the item runs (op_command); `check` marks a checkable item.
 * Generated once from the desktop's own menus; actions.js says which items work on the web. */
export const MENUS = [
 {
  "id": "file",
  "text": "File",
  "menu": [
   {
    "id": "file.new",
    "text": "New...",
    "sc": "Ctrl+N"
   },
   {
    "id": "file.open",
    "text": "Open...",
    "sc": "Ctrl+O"
   },
   {
    "id": "file.open_recent",
    "text": "Open Recent",
    "menu": []
   },
   {
    "id": "file.reopen_closed_tab",
    "text": "Reopen Closed Tab",
    "sc": "Ctrl+Shift+T"
   },
   "-",
   {
    "id": "file.save",
    "text": "Save",
    "sc": "Ctrl+S"
   },
   {
    "id": "file.save_as",
    "text": "Save As...",
    "sc": "Ctrl+Shift+S"
   },
   {
    "id": "file.save_all",
    "text": "Save All"
   },
   {
    "id": "file.close_tab",
    "text": "Close Tab",
    "sc": "Ctrl+F4"
   },
   "-",
   {
    "id": "file.import_vectors",
    "text": "Import Vectors...",
    "sc": "Ctrl+I"
   },
   {
    "id": "file.export_vectors",
    "text": "Export Vectors..."
   },
   "-",
   {
    "id": "file.save_g_code",
    "text": "Save G-code...",
    "sc": "Ctrl+Shift+E"
   },
   {
    "id": "file.open_g_code_backplot",
    "text": "Open G-code (Backplot)..."
   },
   {
    "id": "file.job_report",
    "text": "Job Report..."
   },
   {
    "id": "file.export_image",
    "text": "Export Image..."
   },
   "-",
   {
    "id": "file.quit",
    "text": "Quit"
   }
  ]
 },
 {
  "id": "edit",
  "text": "Edit",
  "menu": [
   {
    "id": "edit.undo",
    "text": "Undo",
    "sc": "Ctrl+Z"
   },
   {
    "id": "edit.redo",
    "text": "Redo",
    "sc": "Ctrl+Y, Ctrl+Shift+Z"
   },
   {
    "id": "edit.repeat",
    "text": "Repeat",
    "sc": "Ctrl+R"
   },
   "-",
   {
    "id": "edit.delete",
    "text": "Delete",
    "sc": "Del"
   },
   {
    "id": "edit.duplicate",
    "text": "Duplicate",
    "sc": "Ctrl+D"
   },
   {
    "id": "edit.cut",
    "text": "Cut",
    "sc": "Ctrl+X"
   },
   {
    "id": "edit.copy",
    "text": "Copy",
    "sc": "Ctrl+C"
   },
   {
    "id": "edit.paste",
    "text": "Paste",
    "sc": "Ctrl+V"
   },
   "-",
   {
    "id": "edit.select",
    "text": "Select",
    "menu": [
     {
      "id": "edit.select.select_all",
      "text": "Select All",
      "sc": "Ctrl+A",
      "cmd": "select.all"
     },
     {
      "id": "edit.select.invert_selection",
      "text": "Invert Selection",
      "sc": "Ctrl+Shift+I",
      "cmd": "select.invert"
     },
     {
      "id": "edit.select.select_open_vectors",
      "text": "Select Open Vectors",
      "cmd": "select.open"
     },
     {
      "id": "edit.select.select_similar",
      "text": "Select Similar...",
      "cmd": "select.similar"
     }
    ]
   },
   "-",
   {
    "id": "edit.preferences",
    "text": "Preferences..."
   },
   {
    "id": "edit.keyboard_shortcuts",
    "text": "Keyboard Shortcuts..."
   }
  ]
 },
 {
  "id": "view",
  "text": "View",
  "menu": [
   {
    "id": "view.zoom_extents",
    "text": "Zoom Extents",
    "sc": "F"
   },
   {
    "id": "view.zoom_selection",
    "text": "Zoom Selection",
    "sc": "Shift+F"
   },
   "-",
   {
    "id": "view.show_toolpaths",
    "text": "Show Toolpaths",
    "check": true
   },
   {
    "id": "view.show_tool_width",
    "text": "Show Tool Width",
    "check": true
   },
   {
    "id": "view.show_directions",
    "text": "Show Directions",
    "check": true
   },
   {
    "id": "view.show_cut_order",
    "text": "Show Cut Order",
    "check": true
   },
   {
    "id": "view.show_route_between_cuts",
    "text": "Show Route Between Cuts",
    "check": true
   },
   {
    "id": "view.show_start_points",
    "text": "Show Start Points",
    "check": true
   },
   {
    "id": "view.show_simulation",
    "text": "Show Simulation",
    "check": true
   },
   "-",
   {
    "id": "view.clear_guides",
    "text": "Clear Guides"
   },
   "-",
   {
    "id": "view.snap_to_grid",
    "text": "Snap to Grid",
    "sc": "F7",
    "check": true
   },
   {
    "id": "view.snap_to_vectors",
    "text": "Snap to Vectors",
    "sc": "F8",
    "check": true
   },
   {
    "id": "view.snap_to_alignments",
    "text": "Snap to Alignments",
    "sc": "Shift+F8",
    "check": true
   },
   {
    "id": "view.snap_to_edges",
    "text": "Snap to Edges",
    "sc": "Shift+F7",
    "check": true
   },
   "-",
   {
    "id": "view.project",
    "text": "Project",
    "check": true
   },
   {
    "id": "view.vector_library",
    "text": "Vector Library",
    "check": true
   },
   {
    "id": "view.selection",
    "text": "Selection",
    "check": true
   },
   {
    "id": "view.transform",
    "text": "Transform",
    "check": true
   },
   {
    "id": "view.toolpaths",
    "text": "Toolpaths",
    "check": true
   },
   {
    "id": "view.cut_order",
    "text": "Cut Order",
    "check": true
   },
   {
    "id": "view.simulation",
    "text": "Simulation",
    "check": true
   },
   {
    "id": "view.python_console",
    "text": "Python Console",
    "check": true
   },
   {
    "id": "view.appearance",
    "text": "Appearance",
    "menu": [
     {
      "id": "view.appearance.follow_the_system",
      "text": "Follow the System",
      "check": true
     },
     {
      "id": "view.appearance.light",
      "text": "Light",
      "check": true
     },
     {
      "id": "view.appearance.dark",
      "text": "Dark",
      "check": true
     }
    ]
   }
  ]
 },
 {
  "id": "draw",
  "text": "Draw",
  "menu": [
   {
    "id": "draw.select_transform",
    "text": "Select / Transform",
    "sc": "S",
    "check": true
   },
   {
    "id": "draw.edit_nodes",
    "text": "Edit Nodes",
    "sc": "N",
    "check": true
   },
   {
    "id": "draw.polyline_bezier",
    "text": "Polyline / Bezier",
    "sc": "P",
    "check": true
   },
   {
    "id": "draw.line",
    "text": "Line",
    "sc": "L",
    "check": true
   },
   {
    "id": "draw.rectangle",
    "text": "Rectangle",
    "sc": "R",
    "check": true
   },
   {
    "id": "draw.circle",
    "text": "Circle",
    "sc": "C",
    "check": true
   },
   {
    "id": "draw.ellipse",
    "text": "Ellipse",
    "sc": "E",
    "check": true
   },
   {
    "id": "draw.polygon",
    "text": "Polygon",
    "sc": "G",
    "check": true
   },
   {
    "id": "draw.star",
    "text": "Star",
    "check": true
   },
   {
    "id": "draw.arc_3_points",
    "text": "Arc (3 points)",
    "sc": "A",
    "check": true
   },
   {
    "id": "draw.text",
    "text": "Text",
    "sc": "T",
    "check": true
   },
   {
    "id": "draw.measure",
    "text": "Measure",
    "sc": "M",
    "check": true
   },
   {
    "id": "draw.trim_vectors",
    "text": "Trim Vectors",
    "sc": "X",
    "check": true
   },
   {
    "id": "draw.set_start_point",
    "text": "Set Start Point",
    "sc": "B",
    "check": true
   }
  ]
 },
 {
  "id": "vectors",
  "text": "Vectors",
  "menu": [
   {
    "id": "vectors.vector_library",
    "text": "Vector Library"
   },
   {
    "id": "vectors.keep_selection_in_the_library",
    "text": "Keep Selection in the Library..."
   },
   "-",
   {
    "id": "vectors.trace_bitmap",
    "text": "Trace Bitmap..."
   },
   {
    "id": "vectors.text_on_curve",
    "text": "Text on Curve..."
   },
   {
    "id": "vectors.start_points",
    "text": "Start Points",
    "menu": [
     {
      "id": "vectors.start_points.set_start_point",
      "text": "Set Start Point",
      "sc": "B",
      "check": true
     },
     "-",
     {
      "id": "vectors.start_points.bottom_left_corner",
      "text": "Bottom Left Corner"
     },
     {
      "id": "vectors.start_points.bottom_right_corner",
      "text": "Bottom Right Corner"
     },
     {
      "id": "vectors.start_points.top_left_corner",
      "text": "Top Left Corner"
     },
     {
      "id": "vectors.start_points.top_right_corner",
      "text": "Top Right Corner"
     },
     {
      "id": "vectors.start_points.middle_of_a_side",
      "text": "Middle of a Side",
      "menu": [
       {
        "id": "vectors.start_points.middle_of_a_side.top_side",
        "text": "Top Side"
       },
       {
        "id": "vectors.start_points.middle_of_a_side.bottom_side",
        "text": "Bottom Side"
       },
       {
        "id": "vectors.start_points.middle_of_a_side.left_side",
        "text": "Left Side"
       },
       {
        "id": "vectors.start_points.middle_of_a_side.right_side",
        "text": "Right Side"
       }
      ]
     },
     "-",
     {
      "id": "vectors.start_points.next_corner",
      "text": "Next Corner",
      "sc": "]"
     },
     {
      "id": "vectors.start_points.previous_corner",
      "text": "Previous Corner",
      "sc": "["
     },
     {
      "id": "vectors.start_points.opposite_point",
      "text": "Opposite Point"
     },
     "-",
     {
      "id": "vectors.start_points.smart_best_place",
      "text": "Smart (best place)"
     },
     {
      "id": "vectors.start_points.release_to_smart_start_points",
      "text": "Release (to smart start points)"
     },
     "-",
     {
      "id": "vectors.start_points.smart_start_points",
      "text": "Smart Start Points..."
     }
    ]
   },
   "-",
   {
    "id": "vectors.create",
    "text": "Create",
    "menu": [
     {
      "id": "vectors.create.rectangle",
      "text": "Rectangle...",
      "cmd": "create.rectangle"
     },
     {
      "id": "vectors.create.circle",
      "text": "Circle...",
      "cmd": "create.circle"
     },
     {
      "id": "vectors.create.ellipse",
      "text": "Ellipse...",
      "cmd": "create.ellipse"
     },
     {
      "id": "vectors.create.polygon",
      "text": "Polygon...",
      "cmd": "create.polygon"
     },
     {
      "id": "vectors.create.star",
      "text": "Star...",
      "cmd": "create.star"
     },
     {
      "id": "vectors.create.slot",
      "text": "Slot...",
      "cmd": "create.slot"
     },
     {
      "id": "vectors.create.arc",
      "text": "Arc...",
      "cmd": "create.arc"
     },
     {
      "id": "vectors.create.spiral",
      "text": "Spiral...",
      "cmd": "create.spiral"
     },
     {
      "id": "vectors.create.bounding_box",
      "text": "Bounding Box",
      "cmd": "create.boundary"
     },
     {
      "id": "vectors.create.door_panel",
      "text": "Door / Panel...",
      "cmd": "create.door"
     }
    ]
   },
   {
    "id": "vectors.edit",
    "text": "Edit",
    "menu": [
     {
      "id": "vectors.edit.offset",
      "text": "Offset...",
      "sc": "Ctrl+Shift+O",
      "cmd": "edit.offset"
     },
     {
      "id": "vectors.edit.weld_union",
      "text": "Weld (Union)",
      "sc": "Ctrl+Shift+W",
      "cmd": "edit.weld"
     },
     {
      "id": "vectors.edit.subtract",
      "text": "Subtract",
      "cmd": "edit.subtract"
     },
     {
      "id": "vectors.edit.intersect",
      "text": "Intersect",
      "cmd": "edit.intersect"
     },
     {
      "id": "vectors.edit.join_vectors",
      "text": "Join Vectors...",
      "sc": "Ctrl+J",
      "cmd": "edit.join"
     },
     {
      "id": "vectors.edit.close_vectors",
      "text": "Close Vectors",
      "cmd": "edit.close"
     },
     {
      "id": "vectors.edit.reverse_direction",
      "text": "Reverse Direction",
      "cmd": "edit.reverse"
     },
     {
      "id": "vectors.edit.group",
      "text": "Group",
      "sc": "Ctrl+G",
      "cmd": "edit.group"
     },
     {
      "id": "vectors.edit.ungroup",
      "text": "Ungroup",
      "sc": "Ctrl+Shift+G",
      "cmd": "edit.ungroup"
     },
     {
      "id": "vectors.edit.explode_into_segments",
      "text": "Explode into Segments",
      "cmd": "edit.explode"
     },
     {
      "id": "vectors.edit.smooth_fit_arcs",
      "text": "Smooth / Fit Arcs...",
      "cmd": "edit.smooth"
     },
     {
      "id": "vectors.edit.simplify",
      "text": "Simplify...",
      "cmd": "edit.simplify"
     },
     {
      "id": "vectors.edit.convert_to_polyline",
      "text": "Convert to Polyline...",
      "cmd": "edit.polyline"
     },
     {
      "id": "vectors.edit.centre_line_single_line",
      "text": "Centre Line (Single Line)...",
      "cmd": "edit.centerline"
     },
     {
      "id": "vectors.edit.vector_doctor",
      "text": "Vector Doctor...",
      "cmd": "edit.doctor"
     },
     {
      "id": "vectors.edit.delete_duplicates",
      "text": "Delete Duplicates...",
      "cmd": "edit.dedupe"
     },
     "-",
     {
      "id": "vectors.edit.resize_handles_mouse",
      "text": "Resize Handles (Mouse)",
      "check": true
     },
     {
      "id": "vectors.edit.arrow_key_step",
      "text": "Arrow Key Step",
      "menu": [
       {
        "id": "vectors.edit.arrow_key_step.automatic_follows_the_zoom",
        "text": "Automatic (follows the zoom)",
        "check": true
       },
       "-",
       {
        "id": "vectors.edit.arrow_key_step.0_1_mm",
        "text": "0.1 mm",
        "check": true
       },
       {
        "id": "vectors.edit.arrow_key_step.1_mm",
        "text": "1 mm",
        "check": true
       },
       {
        "id": "vectors.edit.arrow_key_step.10_mm",
        "text": "10 mm",
        "check": true
       },
       {
        "id": "vectors.edit.arrow_key_step.custom",
        "text": "Custom...",
        "check": true
       }
      ]
     }
    ]
   },
   {
    "id": "vectors.corners",
    "text": "Corners",
    "menu": [
     {
      "id": "vectors.corners.fillet_corners",
      "text": "Fillet Corners...",
      "cmd": "corner.fillet"
     },
     {
      "id": "vectors.corners.chamfer_corners",
      "text": "Chamfer Corners...",
      "cmd": "corner.chamfer"
     },
     {
      "id": "vectors.corners.dog_bone_t_bone_corners",
      "text": "Dog-bone / T-bone Corners...",
      "cmd": "corner.dogbone"
     }
    ]
   },
   {
    "id": "vectors.transform",
    "text": "Transform",
    "menu": [
     {
      "id": "vectors.transform.position_panel",
      "text": "Position Panel",
      "sc": "Alt+F7"
     },
     {
      "id": "vectors.transform.rotate_panel",
      "text": "Rotate Panel",
      "sc": "Alt+F8"
     },
     {
      "id": "vectors.transform.scale_and_mirror_panel",
      "text": "Scale and mirror Panel",
      "sc": "Alt+F9"
     },
     {
      "id": "vectors.transform.size_panel",
      "text": "Size Panel",
      "sc": "Alt+F10"
     },
     {
      "id": "vectors.transform.slant_panel",
      "text": "Slant Panel",
      "sc": "Alt+F11"
     },
     "-",
     {
      "id": "vectors.transform.move",
      "text": "Move...",
      "sc": "Ctrl+M",
      "cmd": "xform.move"
     },
     {
      "id": "vectors.transform.position",
      "text": "Position...",
      "cmd": "xform.move_to"
     },
     {
      "id": "vectors.transform.rotate",
      "text": "Rotate...",
      "sc": "Ctrl+Shift+R",
      "cmd": "xform.rotate"
     },
     {
      "id": "vectors.transform.scale",
      "text": "Scale...",
      "cmd": "xform.scale"
     },
     {
      "id": "vectors.transform.set_size",
      "text": "Set Size...",
      "cmd": "xform.size"
     },
     {
      "id": "vectors.transform.mirror",
      "text": "Mirror...",
      "cmd": "xform.mirror"
     },
     {
      "id": "vectors.transform.slant",
      "text": "Slant...",
      "cmd": "xform.slant"
     },
     {
      "id": "vectors.transform.rotate_90_left",
      "text": "Rotate 90° Left",
      "cmd": "xform.rot90l"
     },
     {
      "id": "vectors.transform.rotate_90_right",
      "text": "Rotate 90° Right",
      "cmd": "xform.rot90r"
     },
     {
      "id": "vectors.transform.rotate_180",
      "text": "Rotate 180°",
      "cmd": "xform.rot180"
     },
     {
      "id": "vectors.transform.flip_left_right",
      "text": "Flip Left / Right",
      "cmd": "xform.flip_h"
     },
     {
      "id": "vectors.transform.flip_top_bottom",
      "text": "Flip Top / Bottom",
      "cmd": "xform.flip_v"
     }
    ]
   },
   {
    "id": "vectors.arrange",
    "text": "Arrange",
    "menu": [
     {
      "id": "vectors.arrange.align",
      "text": "Align...",
      "cmd": "arrange.align"
     },
     {
      "id": "vectors.arrange.centre_on_sheet",
      "text": "Centre on Sheet",
      "sc": "F9",
      "cmd": "arrange.center"
     },
     {
      "id": "vectors.arrange.distribute",
      "text": "Distribute...",
      "cmd": "arrange.distribute"
     },
     {
      "id": "vectors.arrange.rectangular_array",
      "text": "Rectangular Array...",
      "cmd": "arrange.array"
     },
     {
      "id": "vectors.arrange.circular_array",
      "text": "Circular Array...",
      "cmd": "arrange.circular"
     },
     {
      "id": "vectors.arrange.copy_along_vector",
      "text": "Copy Along Vector...",
      "cmd": "arrange.along"
     }
    ]
   }
  ]
 },
 {
  "id": "toolpaths",
  "text": "Toolpaths",
  "menu": [
   {
    "id": "toolpaths.new_toolpath",
    "text": "New Toolpath",
    "menu": [
     {
      "id": "toolpaths.new_toolpath.profile",
      "text": "Profile..."
     },
     {
      "id": "toolpaths.new_toolpath.pocket_area_clearance",
      "text": "Pocket (Area Clearance)..."
     },
     {
      "id": "toolpaths.new_toolpath.drilling",
      "text": "Drilling..."
     },
     {
      "id": "toolpaths.new_toolpath.engrave_follow_vector",
      "text": "Engrave (Follow Vector)..."
     },
     {
      "id": "toolpaths.new_toolpath.v_carve",
      "text": "V-Carve..."
     },
     {
      "id": "toolpaths.new_toolpath.inlay",
      "text": "Inlay..."
     },
     {
      "id": "toolpaths.new_toolpath.fluting",
      "text": "Fluting..."
     }
    ]
   },
   {
    "id": "toolpaths.toolpath_packages",
    "text": "Toolpath Packages..."
   },
   {
    "id": "toolpaths.save_selected_toolpaths_as_a_package",
    "text": "Save Selected Toolpaths as a Package..."
   },
   {
    "id": "toolpaths.use_a_package",
    "text": "Use a Package",
    "menu": []
   },
   {
    "id": "toolpaths.recalculate_all",
    "text": "Recalculate All",
    "sc": "F5"
   },
   {
    "id": "toolpaths.cut_order",
    "text": "Cut Order...",
    "sc": "Ctrl+Alt+O"
   },
   {
    "id": "toolpaths.cut_inner_pieces_g_bek_first",
    "text": "Cut Inner Pieces (Göbek) First",
    "check": true
   },
   "-",
   {
    "id": "toolpaths.material_setup",
    "text": "Material Setup...",
    "sc": "Ctrl+Shift+M"
   },
   {
    "id": "toolpaths.tool_database",
    "text": "Tool Database..."
   },
   "-",
   {
    "id": "toolpaths.simulate",
    "text": "Simulate",
    "sc": "F6"
   },
   {
    "id": "toolpaths.clear_simulation",
    "text": "Clear Simulation"
   },
   {
    "id": "toolpaths.clear_g_code_backplot",
    "text": "Clear G-code Backplot"
   },
   "-",
   {
    "id": "toolpaths.save_toolpath_template",
    "text": "Save Toolpath Template..."
   },
   {
    "id": "toolpaths.load_toolpath_template",
    "text": "Load Toolpath Template..."
   },
   "-",
   {
    "id": "toolpaths.save_g_code",
    "text": "Save G-code..."
   },
   {
    "id": "toolpaths.post_processors",
    "text": "Post-processors..."
   }
  ]
 },
 {
  "id": "nesting",
  "text": "Nesting",
  "menu": [
   {
    "id": "nesting.nest_parts",
    "text": "Nest Parts...",
    "sc": "Ctrl+Shift+N"
   },
   "-",
   {
    "id": "nesting.stock_of_plates",
    "text": "Stock of Plates..."
   },
   {
    "id": "nesting.add_selected_vectors_to_stock",
    "text": "Add Selected Vectors to Stock"
   },
   {
    "id": "nesting.add_leftover_of_current_sheet_to_stock",
    "text": "Add Leftover of Current Sheet to Stock..."
   },
   "-",
   {
    "id": "nesting.set_sheet_outline_from_selected_vectors",
    "text": "Set Sheet Outline from Selected Vectors"
   },
   {
    "id": "nesting.clear_sheet_outline",
    "text": "Clear Sheet Outline"
   }
  ]
 },
 {
  "id": "plugins",
  "text": "Plugins",
  "menu": [
   {
    "id": "plugins.plugin_manager",
    "text": "Plugin Manager..."
   },
   {
    "id": "plugins.python_console",
    "text": "Python Console",
    "sc": "Ctrl+`"
   },
   "-"
  ]
 },
 {
  "id": "window",
  "text": "Window",
  "menu": []
 },
 {
  "id": "help",
  "text": "Help",
  "menu": [
   {
    "id": "help.keyboard_shortcuts",
    "text": "Keyboard Shortcuts"
   },
   {
    "id": "help.g_code_words_i_j_k",
    "text": "G-code Words (I J K ...)..."
   },
   {
    "id": "help.about_iyicam",
    "text": "About iyiCAM"
   }
  ]
 }
];
