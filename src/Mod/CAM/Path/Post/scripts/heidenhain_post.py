# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2014 sliptonic <shopinthewoods@gmail.com>               *
# *   Copyright (c) 2022 - 2025 Larry Woestman <LarryWoestman2@gmail.com>   *
# *   Copyright (c) 2024 Ondsel <development@ondsel.com>                    *
# *   Copyright (c) 2024 Carl Slater <CandLWorkshopLLC@gmail.com>           *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful,            *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Lesser General Public License for more details.                   *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with FreeCAD; if not, write to the Free Software        *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

import datetime
import os
from typing import Any, Dict, Tuple, List

import Path.Base.Util as PathUtil
import Path.Post.Utils as PostUtils
import Path.Tool.Controller as PathToolController
from Path import Command
from Path.Post.Processor import (
    GCodeOrNone,
    Postables,
    PostProcessor,
    GCodeSections,
    Section,
    Sublist,
)
import Path.Post.UtilsExport as ExportUtils
from Path.Post.UtilsExport import Gcode, Values
import Path.Post.UtilsParse as ParseUtils
from PathScripts.PathUtils import findParentJob

import Path
import FreeCAD
from FreeCAD import Units

translate = FreeCAD.Qt.translate

debug = True
if debug:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())

    # Clear the Report View for the processor output. (Usefull while developing)
    from PySide import QtGui

    FreeCAD.Gui.getMainWindow().findChild(QtGui.QTextEdit, "Report view").clear()

else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


# *****************************************
# *      Heidenhain Cleartext example     *
# *****************************************
# *                                       *
# * 0 BEGIN PGM JOB MM                    *
# * 1 TOOL CALL 2 Z S4000 ;5mm Endmill001 *
# * 2 M6 ;Execute TOOL CALL               *
# * 3 L Z6.000 R0 F MAX M3                *
# * 4 L X1.506 Y0.100 F MAX               *
# * 5 L Z4.000 F MAX                      *
# * ...                                   *
# * 58 CC X49.997 Y-10.001                *
# * 59 C X62.498 Y-9.891 DR-              *
# * 60 L X62.500 Y-50.000                 *
# * 61 CC X49.999 Y-49.997                *
# * 62 C X50.109 Y-62.498 DR-             *
# * 63 L X10.000 Y-62.500                 *
# * ...                                   *
# * 185 L Z6.000 F MAX                    *
# * 186 M30                               *
# * 187 END PGM JOB MM                    *
# *                                       *
# *****************************************
class Refactored_Heidenhain(PostProcessor):
    """The Heidenhain post processor class."""

    def __init__(
        self,
        job,
        tooltip=translate("CAM", "Refactored Heidenhain post processor"),
        tooltipargs=[""],
        units="Metric",
    ) -> None:
        super().__init__(
            job=job,
            tooltip=tooltip,
            tooltipargs=tooltipargs,
            units=units,
        )
        Path.Log.debug("Refactored Heidenhain post processor initialized.")

    def init_values(self, values: Values) -> None:
        """Initialize values that are used throughout the postprocessor."""

        super().init_values(values)

        #
        # Set any values here that need to override the default values set
        # in the parent routine.
        #

        #
        # Used in the argparser code as the "name" of the postprocessor program.
        #
        values["MACHINE_NAME"] = "Heidenhain"
        values["POSTPROCESSOR_FILE_NAME"] = __name__

        #
        # Setup parameters that can be changed via arguments
        #
        values["OUTPUT_DOUBLES"] = True
        values["LIST_TOOLS_IN_PREAMBLE"] = True
        values["OUTPUT_LINE_NUMBERS"] = True

        #
        # The order of parameters. (Not clearly defined in the manuals)
        # "X", "Y", "Z", "A", "B", "C": Store machine positions and rotations
        # "I", "J", "K": Not directly output in the arc but used for calculation
        # "R": Tool radius compensation; "R0", "RL" or "RR" (Not implemented yet)
        # "D": Arc direction; "DR+" (CCW), "DR-" (CW)
        # "F": Feedrate
        # "S": Spindle speed
        # "M": Possible M parameter
        #
        values["PARAMETER_ORDER"] = [
            "X",
            "Y",
            "Z",
            "A",
            "B",
            "C",
            "I",
            "J",
            "K",
            "R",
            "D",
            "F",
            "S",
            "M",
        ]

        values["line_number"] = 1
        values["LINE_INCREMENT"] = 1

        #
        # Setup parameters custom to this post-processor
        # TODO: Add arguments
        #
        values["PGM_UPPERCASE"] = True
        values["SUPPORT_CYLINDER_STOCK"] = False
        values["DECIMAL_COMMAS"] = True
        values["NORMALIZE_PARAMETERS"] = True
        values["LINE_PREFIX"] = ""
        values["LINE_POSTFIX"] = "  "
        values["LINE_NUMBER_ON_COMMENTS"] = True
        values["COMMENT_PREFIX"] = "; ("
        values["COMMENT_POSTFIX"] = ")"

        values["ENABLE_COOLANT"] = True
        values["SUPPORT_FLOOD_COOLANT"] = True
        values["SUPPORT_MIST_COOLANT"] = False

        values["USE_FMAX"] = True
        values["FMAX_SPEED"] = 8000  # mm/min

        values["spindle_state"] = "None"
        values["spindle_dir"] = "None"
        values["coolant_mode"] = "None"

    def init_argument_defaults(self, argument_defaults: Dict[str, bool]) -> None:
        """Initialize which arguments (in a pair) are shown as the default argument."""
        super().init_argument_defaults(argument_defaults)
        #
        # Modify which argument to show as the default in flag-type arguments here.
        # If the value is True, the first argument will be shown as the default.
        # If the value is False, the second argument will be shown as the default.
        #
        # For example, if you want to show Metric mode as the default, use:
        #   argument_defaults["metric_inch"] = True
        #
        # If you want to show that "Don't pop up editor for writing output" is
        # the default, use:
        #   argument_defaults["show-editor"] = False.
        #
        # Note:  You also need to modify the corresponding entries in the "values" hash
        #        to actually make the default value(s) change to match.
        #

        argument_defaults["axis-modal"] = True
        # argument_defaults["bcnc"] = False
        # argument_defaults["comments"] = True
        # argument_defaults["enable_coolant"] = False
        # argument_defaults["enable_machine_specific_commands"] = False
        # argument_defaults["header"] = True
        argument_defaults["line-numbers"] = True
        argument_defaults["list_tools_in_preamble"] = True
        # argument_defaults["metric_inches"] = True
        # argument_defaults["modal"] = False
        # argument_defaults["output_all_arguments"] = False
        # argument_defaults["output_machine_name"] = False
        # argument_defaults["output_path_labels"] = False
        # argument_defaults["output_visible_arguments"] = False
        # argument_defaults["show-editor"] = True
        # argument_defaults["tlo"] = True
        # argument_defaults["tool_change"] = True
        # argument_defaults["translate_drill"] = False

    def process_postables(self) -> GCodeSections:
        """Postprocess the 'postables' in the job to g code sections."""
        #
        # This function is separated out to make it easier to inherit from this class.
        #
        gcode: GCodeOrNone
        g_code_sections: GCodeSections
        partname: str
        postables: Postables
        section: Section
        sublist: Sublist

        postables = self._buildPostList()

        Path.Log.debug(f"postables count: {len(postables)}")

        g_code_sections = []
        for _, section in enumerate(postables):
            partname, sublist = section
            Path.Log.debug(f"section: {section}")
            Path.Log.debug(f"section: {sublist}")
            gcode = export(self.values, sublist, "-")
            g_code_sections.append((partname, gcode))

        return g_code_sections

    @property
    def tooltip(self):
        tooltip: str = """
        This is a postprocessor file for the CAM workbench.
        It is used to take a pseudo-gcode fragment from a CAM object
        and output 'real' GCode suitable for a Heidenhain 3 axis mill.
        """
        return tooltip


def export(values: Values, objectslist: List, filename: str) -> str:
    """Custom export function for exporting Heidenhain Cleartext"""
    gcode: Gcode = []

    for obj in objectslist:
        if not hasattr(obj, "Path"):
            print(f"The object {obj.Name} is not a path.")
            print("Please select only path and Compounds.")
            return None

    if len(objectslist) == 0:
        Path.Log.error("No objects to export! (Please select a Fixture like 'G54')")
        return None

    # Find the current job all opperations belong to, index 0 should contains the Fixture
    current_job = findParentJob(objectslist[0])

    print(f'PostProcessor:  {values["POSTPROCESSOR_FILE_NAME"]} postprocessing...')

    # check_canned_cycles(values)
    output_header(values, gcode)
    output_safetyblock(values, gcode)
    output_tool_list(values, gcode, objectslist)
    output_preamble(values, gcode)
    # output_motion_mode(values, gcode)
    # output_units(values, gcode)

    output_pgm_begin(values, gcode, current_job)
    output_stock_definition(values, gcode, current_job)

    for obj in objectslist:
        # Skip inactive operations
        if not PathUtil.activeForOp(obj):
            continue

        output_start_bcnc(values, gcode, obj)
        output_preop(values, gcode, obj)

        output_coolant_update(values, gcode, obj)

        # output the G-code for the group (compound) or simple path
        parse_a_group(values, gcode, obj)
        output_postop(values, gcode, obj)

    # output_return_to(values, gcode)
    # output_end_bcnc(values, gcode)
    output_pgm_end(values, gcode, current_job)

    output_postamble_header(values, gcode)
    output_tool_return(values, gcode)
    output_safetyblock(values, gcode)
    output_postamble(values, gcode)

    add_linenumbers(values, gcode)

    ExportUtils.finalize_export(values, gcode, filename)


# *********************************************************
# * Exporting Cleartext requires rewriting all of this... *
# *********************************************************


def output_pgm_begin(values: Values, gcode: Gcode, current_job) -> None:
    """Output the begin line for the program"""

    gcode.append(f"BEGIN PGM " + pgm_job_name(values, current_job) + " " + pgm_unit(values))


def output_pgm_end(values: Values, gcode: Gcode, current_job) -> None:
    """Output the end line for the program"""

    gcode.append(f"END PGM " + pgm_job_name(values, current_job) + " " + pgm_unit(values))


def pgm_job_name(values: Values, current_job) -> str:
    program_name: str = "New"

    if hasattr(current_job, "Label"):
        program_name = current_job.Label

    if values["PGM_UPPERCASE"]:
        program_name = program_name.upper()

    return program_name


def pgm_unit(values: Values) -> str:
    if values["UNITS"] == "G21":
        return "MM"
    elif values["UNITS"] == "G20":
        return "INCH"
    else:
        return ""


def output_stock_definition(values: Values, gcode: Gcode, current_job) -> None:
    """Output the stock definition for the program"""
    stock_type: str = ""
    size: Dict = {}
    offset: Dict = {}

    # If the job has no stock defined, skip the stock definition
    if not hasattr(current_job, "Stock"):
        return

    stock = current_job.Stock

    # Get the offset of the stock material
    if hasattr(stock, "Placement") and hasattr(stock.Placement, "Base"):
        base = stock.Placement.Base
        offset["X"] = base.x
        offset["Y"] = base.y
        offset["Z"] = base.z

    # Handle diffrent stock types
    match stock.StockType:
        case "CreateBox":
            stock_type = "Box"
            size["X"] = stock.Length.Value
            size["Y"] = stock.Width.Value
            size["Z"] = stock.Height.Value
        case "CreateCylinder":
            if values["SUPPORT_CYLINDER_STOCK"]:
                stock_type = "Cylinder"
                size["R"] = stock.Radius.Value
                size["L"] = stock.Height.Value
            else:
                stock_type = "Box"
                offset["X"] = offset["X"] - stock.Radius.Value
                offset["Y"] = offset["Y"] - stock.Radius.Value
                size["X"] = stock.Radius.Value * 2
                size["Y"] = stock.Radius.Value * 2
                size["Z"] = stock.Height.Value
        case "FromBase":
            stock_type = "Box"
            bounding_box = Path.Main.Stock.shapeBoundBox(stock)
            offset["X"] = bounding_box.XMin
            offset["Y"] = bounding_box.YMin
            offset["Z"] = bounding_box.ZMin
            size["X"] = -bounding_box.XMin + bounding_box.XMax
            size["Y"] = -bounding_box.YMin + bounding_box.YMax
            size["Z"] = -bounding_box.ZMin + bounding_box.ZMax
        case "Unknown":
            Path.Log.warning("Custom stock type is currently not supported.")
            return
        case "None":
            return

    # TODO: Does not take into account other coordinate systems than Z-up.
    if stock_type == "Box":
        gcode.append(
            "BLK FORM 0.1 Z X{} Y{} Z{}".format(
                format_for_axis(values, Units.Quantity(offset["X"], Units.Length)),
                format_for_axis(values, Units.Quantity(offset["Y"], Units.Length)),
                format_for_axis(values, Units.Quantity(offset["Z"], Units.Length)),
            )
        )
        gcode.append(
            "BLK FORM 0.2 X{} Y{} Z{}".format(
                format_for_axis(values, Units.Quantity(size["X"] + offset["X"], Units.Length)),
                format_for_axis(values, Units.Quantity(size["Y"] + offset["Y"], Units.Length)),
                format_for_axis(values, Units.Quantity(size["Z"] + offset["Z"], Units.Length)),
            ),
        )
    elif stock_type == "Cylinder":
        gcode.append(
            "BLK FORM CYLINDER Z R{} L{} DIST{}".format(
                format_for_axis(values, Units.Quantity(size["R"], Units.Length)),
                format_for_axis(values, Units.Quantity(size["L"], Units.Length)),
                format_for_axis(values, Units.Quantity(offset["Z"], Units.Length)),
            )
        )


# def check_canned_cycles(values: Values) -> None:
#     """Check canned cycles for drilling."""

#     if values["TRANSLATE_DRILL_CYCLES"]:
#         if len(values["SUPPRESS_COMMANDS"]) == 0:
#             values["SUPPRESS_COMMANDS"] = ["G99", "G98", "G80"]
#         else:
#             values["SUPPRESS_COMMANDS"] += ["G99", "G98", "G80"]


# def output_coolant_off(values: Values, gcode: Gcode, coolant_mode: str) -> None:
#     """Output the commands to turn coolant off if necessary."""
#     comment: str

#     if values["ENABLE_COOLANT"] and coolant_mode != "None":
#         if values["OUTPUT_COMMENTS"]:
#             comment = create_comment(values, f"Coolant Off: {coolant_mode}")
#             gcode.append(f"{comment}")
#         gcode.append("M9")


def output_coolant_update(values: Values, gcode: Gcode, obj) -> None:
    """Output the commands to turn coolant on if necessary."""
    new_coolant_mode: str = PathUtil.coolantModeForOp(obj)
    prev_coolant_mode: str = values["coolant_mode"]
    used_coolant_mode: str = None
    command: str = None

    if values["ENABLE_COOLANT"] and prev_coolant_mode != new_coolant_mode:
        match (new_coolant_mode, values["SUPPORT_FLOOD_COOLANT"], values["SUPPORT_MIST_COOLANT"]):
            case ("None", _, _):
                used_coolant_mode = "None"
                command = "M9"
            case ("Flood", True, _) | ("Mist", True, False):
                used_coolant_mode = "Flood"
                command = "M8"
            case ("Mist", _, True) | ("Flood", False, True):
                used_coolant_mode = "Mist"
                command = "M7"
            case (_, _, _):
                Path.Log.warning("Coolant enabled but no coolant options are supported!")

        if not command:
            return

        if values["OUTPUT_COMMENTS"]:
            if new_coolant_mode == "None":
                comment = "Coolant Off"
            elif new_coolant_mode == used_coolant_mode:
                comment = f"Coolant On: {new_coolant_mode}"
            else:
                comment = f"Coolant On: {new_coolant_mode} not available, using {used_coolant_mode}"
            gcode.append(create_comment(values, comment))

        values["coolant_mode"] = used_coolant_mode

        gcode.append(command)


def output_end_bcnc(values: Values, gcode: Gcode) -> None:
    """Output the ending BCNC header."""
    comment: str

    if values["OUTPUT_BCNC"]:
        comment = create_comment(values, "Block-name: post_amble")
        gcode.append(f"{comment}")
        comment = create_comment(values, "Block-expand: 0")
        gcode.append(f"{comment}")
        comment = create_comment(values, "Block-enable: 1")
        gcode.append(f"{comment}")


def output_header(values: Values, gcode: Gcode) -> None:
    """Output the header."""
    cam_file: str

    if not values["OUTPUT_HEADER"]:
        return

    gcode.append(create_comment(values, "Exported by FreeCAD"))
    gcode.append(create_comment(values, f'Post Processor: {values["POSTPROCESSOR_FILE_NAME"]}'))
    if FreeCAD.ActiveDocument:
        cam_file = os.path.basename(FreeCAD.ActiveDocument.FileName)
    else:
        cam_file = "<None>"
    gcode.append(create_comment(values, f"Cam File: {cam_file}"))
    gcode.append(create_comment(values, f"Output Time: {str(datetime.datetime.now())}"))


def output_motion_mode(values: Values, gcode: Gcode) -> None:
    """Verify if PREAMBLE or SAFETYBLOCK have changed MOTION_MODE."""

    if "G90" in values["PREAMBLE"] or "G90" in values["SAFETYBLOCK"]:
        values["MOTION_MODE"] = "G90"
    elif "G91" in values["PREAMBLE"] or "G91" in values["SAFETYBLOCK"]:
        values["MOTION_MODE"] = "G91"
    else:
        gcode.append(f'{values["MOTION_MODE"]}')


def output_postamble_header(values: Values, gcode: Gcode) -> None:
    """Output the postamble header."""
    comment: str = ""

    if values["OUTPUT_COMMENTS"]:
        comment = create_comment(values, "Begin postamble")
        gcode.append(f"{comment}")


def output_postamble(values: Values, gcode: Gcode) -> None:
    """Output the postamble."""
    line: str

    for line in values["POSTAMBLE"].splitlines(False):
        gcode.append(f"{line}")


def output_postop(values: Values, gcode: Gcode, obj) -> None:
    """Output the post-operation information."""
    comment: str
    line: str

    if values["OUTPUT_COMMENTS"]:
        if values["SHOW_OPERATION_LABELS"]:
            comment = create_comment(values, f'{values["FINISH_LABEL"]} operation: {obj.Label}')
        else:
            comment = create_comment(values, f'{values["FINISH_LABEL"]} operation')
        gcode.append(f"{comment}")
    for line in values["POST_OPERATION"].splitlines(False):
        gcode.append(f"{line}")


def output_preamble(values: Values, gcode: Gcode) -> None:
    """Output the preamble."""
    line: str

    if values["OUTPUT_COMMENTS"]:
        gcode.append(create_comment(values, "Begin preamble"))
    for line in values["PREAMBLE"].splitlines(False):
        gcode.append(line)


def output_preop(values: Values, gcode: Gcode, obj) -> None:
    """Output the pre-operation information."""
    comment: str
    line: str

    if values["OUTPUT_COMMENTS"]:
        if values["SHOW_OPERATION_LABELS"]:
            comment = create_comment(values, f"Begin operation: {obj.Label}")
        else:
            comment = create_comment(values, "Begin operation")
        gcode.append(f"{comment}")
        if values["SHOW_MACHINE_UNITS"]:
            comment = create_comment(values, f'Machine units: {values["UNIT_SPEED_FORMAT"]}')
            gcode.append(f"{comment}")
        if values["OUTPUT_MACHINE_NAME"]:
            comment = create_comment(
                values,
                f'Machine: {values["MACHINE_NAME"]}, {values["UNIT_SPEED_FORMAT"]}',
            )
            gcode.append(f"{comment}")
    for line in values["PRE_OPERATION"].splitlines(False):
        gcode.append(f"{line}")


# def output_return_to(values: Values, gcode: Gcode) -> None:
#     """Output the RETURN_TO command."""
#     cmd: str
#     num_x: str
#     num_y: str
#     num_z: str

#     if values["RETURN_TO"]:
#         num_x = values["RETURN_TO"][0]
#         num_y = values["RETURN_TO"][1]
#         num_z = values["RETURN_TO"][2]
#         cmd = format_command_line(values, ["G0", f"X{num_x}", f"Y{num_y}", f"Z{num_z}"])
#         gcode.append(f"{cmd}")


def output_safetyblock(values: Values, gcode: Gcode) -> None:
    """Output the safety block."""
    line: str

    for line in values["SAFETYBLOCK"].splitlines(False):
        gcode.append(f"{line}")


def output_start_bcnc(values: Values, gcode: Gcode, obj) -> None:
    """Output the starting BCNC header."""

    if values["OUTPUT_BCNC"]:
        gcode.append(create_comment(values, f"Block-name: {obj.Label}"))
        gcode.append(create_comment(values, "Block-expand: 0"))
        gcode.append(create_comment(values, "Block-enable: 1"))


def output_tool_list(values: Values, gcode: Gcode, objectslist) -> None:
    """Output a list of the tools used in the objects."""

    if values["OUTPUT_COMMENTS"] and values["LIST_TOOLS_IN_PREAMBLE"]:
        for item in objectslist:
            if hasattr(item, "Proxy") and isinstance(item.Proxy, PathToolController.ToolController):
                gcode.append(create_comment(values, f"T{item.ToolNumber}={item.Label}"))


def output_tool_return(values: Values, gcode: Gcode) -> None:
    """Output the tool return block."""
    line: str

    for line in values["TOOLRETURN"].splitlines(False):
        gcode.append(f"{line}")


# def output_units(values: Values, gcode: Gcode) -> None:
#     """Verify if PREAMBLE or SAFETYBLOCK have changed UNITS."""

#     if "G21" in values["PREAMBLE"] or "G21" in values["SAFETYBLOCK"]:
#         values["UNITS"] = "G21"
#         values["UNIT_FORMAT"] = "mm"
#         values["UNIT_SPEED_FORMAT"] = "mm/min"
#     elif "G20" in values["PREAMBLE"] or "G20" in values["SAFETYBLOCK"]:
#         values["UNITS"] = "G20"
#         values["UNIT_FORMAT"] = "in"
#         values["UNIT_SPEED_FORMAT"] = "in/min"
#     else:
#         gcode.append(f'{values["UNITS"]}')


def add_linenumbers(values: Values, gcode: Gcode) -> None:
    """Add linenumbers to every line of gcode."""
    if not values["OUTPUT_LINE_NUMBERS"]:
        return

    for i, line in enumerate(gcode):
        if (
            not line.startswith(values["COMMENT_PREFIX"])
            and not values["LINE_NUMBER_ON_COMMENTS"]
            or values["LINE_NUMBER_ON_COMMENTS"]
        ):
            gcode[i] = (
                values["LINE_PREFIX"] + str(values["line_number"]) + values["LINE_POSTFIX"] + line
            )
            values["line_number"] += values["LINE_INCREMENT"]


def create_comment(values: Values, comment_string: str) -> str:
    """Create a comment from a string using the correct pre and postfixes."""
    return values["COMMENT_PREFIX"] + comment_string + values["COMMENT_POSTFIX"]


def format_for_axis(values: Values, number: Units.Quantity) -> str:
    """Format a number using the precision for an axis value."""
    return format_parameter(
        values,
        format(float(number.getValueAs(values["UNIT_FORMAT"])), f'+.{values["AXIS_PRECISION"]}f'),
    )


def format_for_feed(values: Values, number: Units.Quantity) -> str:
    """Format a number using the precision for a feed rate."""
    return format_parameter(
        values,
        format(
            float(number.getValueAs(values["UNIT_SPEED_FORMAT"])), f'.{values["FEED_PRECISION"]}f'
        ),
    )


def format_for_spindle(values: Values, number: Units.Quantity) -> str:
    """Format a number using the precision for a spindle speed."""
    return format_parameter(values, format(float(number), f'.{values["SPINDLE_DECIMALS"]}f'))


def format_parameter(values: Values, value: str) -> str:
    """Do a final format on the output string for any parameter"""
    # Strip trailing zeros and possible decimal point
    if values["NORMALIZE_PARAMETERS"] and "." in value:
        value = value.rstrip("0").rstrip(".")

    # Swap points for commas
    if values["DECIMAL_COMMAS"]:
        value = value.replace(".", ",")

    return value


def parse_a_group(values: Values, gcode: Gcode, pathobj) -> None:
    """Parse a Group (compound, project, or simple path)."""

    if hasattr(pathobj, "Group"):  # We have a compound or project.
        if values["OUTPUT_COMMENTS"]:
            gcode.append(create_comment(values, f"Compound: {pathobj.Label}"))
        for p in pathobj.Group:
            parse_a_group(values, gcode, p)
    else:  # Parsing a simple path
        # Groups might contain non-path things like stock.
        if not hasattr(pathobj, "Path"):
            return
        if values["OUTPUT_PATH_LABELS"] and values["OUTPUT_COMMENTS"]:
            gcode.append(create_comment(values, f"Compound: {pathobj.Label}"))
        parse_a_path(values, gcode, pathobj)


def parse_a_path(values: Values, gcode: Gcode, pathobj) -> None:
    """Parse a simple Path."""
    adaptive_op_variables: Tuple[bool, float, float]
    cmd: str
    command_name: str
    command_line: ParseUtils.CommandLine
    current_parameters: ParseUtils.PathParameters = {}  # Keep track for no doubles
    drill_retract_mode: str = "G98"
    lastcommand: str = ""
    motion_location: ParseUtils.PathParameters = {}  # Keep track of last motion location
    parameter: str
    parameter_value: str

    # Check to see if the opperation is a fixture
    # TODO: Implement fixture support, skip for now
    if "Fixture" in pathobj.Name:
        Path.Log.warning("Fixtures not yet supported. Please make sure offsets are correct!")
        return None

    # print(f"Object: {pathobj.Label} ({pathobj.Name})")
    # # print(f"Object: {pathobj.Path.Commands}")
    print(f"Object: {type(pathobj.Proxy)}\n")
    # print(f"Object: {isinstance(pathobj.Proxy, )}\n")

    # Check to see if values["TOOL_BEFORE_CHANGE"] is set and value is true
    # doing it here to reduce the number of times it is checked
    swap_tool_change_order = False
    if "TOOL_BEFORE_CHANGE" in values and values["TOOL_BEFORE_CHANGE"]:
        swap_tool_change_order = True

    # The goal is to have initial values that aren't likely to match
    # any "real" first parameter values
    # NOTE: ParseUtils.PathParameters are defined as Dict[str, float]
    #       So strings for "R" and "D" are not realy allowed
    current_parameters.update(
        {
            "X": 123456789.0,
            "Y": 123456789.0,
            "Z": 123456789.0,
            "A": 123456789.0,
            "B": 123456789.0,
            "C": 123456789.0,
            "I": 123456789.0,
            "J": 123456789.0,
            "K": 123456789.0,
            "R": "",
            "D": "",
            "F": 123456789.0,
            "S": 123456789.0,
        }
    )
    adaptive_op_variables = ParseUtils.determine_adaptive_op(values, pathobj)

    for command in pathobj.Path.Commands:
        command_name = command.Name
        command_line = []

        # Normalize: G0x -> Gx & M0x -> Mx
        if command_name != "G0":
            command_name = command_name.replace("G0", "G")
        if command_name != "M0":
            command_name = command_name.replace("M0", "M")

        # Skip blank lines if requested
        if not command_name:
            if not values["OUTPUT_BLANK_LINES"]:
                continue

        # # NOTE: This does not make sense with the new comment pre- postfix system
        # # Modify the command name if necessary
        # if command.startswith("("):
        #     if not values["OUTPUT_COMMENTS"]:
        #         continue
        #     if values["COMMENT_SYMBOL"] != "(" and len(command) > 2:
        #         command = create_comment(values, command[1:-1])

        # TODO: implement adaptive opperations
        # cmd = ParseUtils.check_for_an_adaptive_op(values, command, command_line, adaptive_op_variables)
        # if cmd:
        #     command = cmd

        # Add the command name to the command line
        # command_line.append(command)

        # # If modal: suppress the command if it is the same as the last one
        # if values["MODAL"] and command == lastcommand:
        #     command_line.pop(0)

        # # Now add the remaining parameters in order
        # for parameter in values["PARAMETER_ORDER"]:
        #     if parameter in command.Parameters:
        #         parameter_value = values["PARAMETER_FUNCTIONS"][parameter](
        #             values,
        #             command_name,
        #             parameter,
        #             command.Parameters[parameter],
        #             command.Parameters,
        #             current_parameters,
        #         )
        #         if parameter_value:
        #             command_line.append(f"{parameter}{parameter_value}")

        # TODO: implement adaptive opperations
        # ParseUtils.set_adaptive_op_speed(values, command, command_line, c.Parameters, adaptive_op_variables)

        # # Remember the current command
        # lastcommand = command
        # # Remember the current location
        # current_parameters.update(c.Parameters)

        if command_name in ("G90", "G91"):
            # Remember the motion mode
            values["MOTION_MODE"] = command_name
            Path.Log.warning("Only ABSOLUTE positioning is currently suported")
        elif command_name in ("G98", "G99"):
            # Remember the drill retract mode for drill_translate
            drill_retract_mode = command_name

        # if command in values["MOTION_COMMANDS"]:
        #     # Remember the current location for drill_translate
        #     motion_location.update(c.Parameters)

        # if ParseUtils.check_for_drill_translate(
        #     values,
        #     gcode,
        #     command,
        #     command_line,
        #     c.Parameters,
        #     motion_location,
        #     drill_retract_mode,
        # ):
        #     command_line = []

        # ParseUtils.check_for_spindle_wait(values, gcode, command, command_line)

        # Process G-codes
        match command_name:
            case "G0" | "G1":
                output_line(values, gcode, command, current_parameters)
            case "G2" | "G3":
                output_arc(values, gcode, command, current_parameters)

        # Process M-codes
        match command_name:
            case "M1":
                output_optional_stop(values, gcode)
            case "M2" | "M30":
                output_forced_stop(values, gcode)
            case "M6":
                output_tool_change(values, gcode, pathobj)

        # if ParseUtils.check_for_suppressed_commands(values, gcode, command, command_line):
        #     command_line = []

        # if command_line:
        #     if command in ("M6", "M06") and swap_tool_change_order:
        #         swapped_command_line = [
        #             command_line[1],
        #             command_line[0],
        #         ]
        #         # swap the order of the commands
        #         # Add a line number to the front of the command line
        #         # gcode.append(ParseUtils.format_command_line(values, swapped_command_line))
        #     else:
        #     # Add a line number to the front of the command line
        # gcode.append(ParseUtils.format_command_line(values, command_line))

        # ParseUtils.check_for_tlo(values, gcode, command, c.Parameters)
        # ParseUtils.check_for_machine_specific_commands(values, gcode, command)


def output_line(
    values: Values, gcode: Gcode, command: Command, current_parameters: ParseUtils.PathParameters
) -> None:
    """Output a linear movement."""

    FMAX_SPEED = values["FMAX_SPEED"] / 60  # mm/min / 60 = mm/s
    line = "L"

    # TODO: Implement tool radius compensation
    command.Parameters["R"] = "0"
    # if COMPENSATION_DIFF_STATUS[0]:  # Diff from compensated ad not compensated path
    #     if COMPENSATION_DIFF_STATUS[1]:  # skip if already compensated, not active by now
    #         Cmd_Number -= 1  # align
    #         # initialize like true, set false if not same point compensated and not compensated
    #         i = True
    #         for j in H_Line_Params[0]:
    #             if j in STORED_COMPENSATED_OBJ[Cmd_Number].Parameters and j in line_Params:
    #                 if STORED_COMPENSATED_OBJ[Cmd_Number].Parameters[j] != line_Params[j]:
    #                     i = False
    #         if i == False:
    #             H_Line_Params[1][0] = "R" + line_comp
    #     #                   we can skip this control if already in compensation
    #     #                   COMPENSATION_DIFF_STATUS[1] = False
    #     else:
    #         H_Line_Params[1][0] = "R" + line_comp  # not used by now

    for p in values["PARAMETER_ORDER"]:
        # Add the F parameter if command is rapid feed as it is not included
        if command.Name == "G0":
            command.Parameters["F"] = FMAX_SPEED

        if p in command.Parameters:
            match p:
                case "X" | "Y" | "Z" | "A" | "B" | "C":
                    if values["MOTION_MODE"] == "G91":  # Incremental
                        current_parameters[p] = 0
                        # H_Line_New[i] = 0
                        # if i in line_Params:
                        #     if line_Params[i] != 0 or line_M_funct != "":
                        #         H_Line += " I" + HEIDEN_Format(i, line_Params[i])  # print incremental
                        # # update to absolute position
                        # H_Line_New[i] = MACHINE_LAST_POSITION[i] + H_Line_New[i]
                    else:  # Absolute
                        if command.Parameters[p] != current_parameters[p]:  # or line_M_funct != "":
                            line += (
                                " "
                                + p
                                + format_for_axis(
                                    values, Units.Quantity(command.Parameters[p], Units.Length)
                                )
                            )
                            current_parameters[p] = command.Parameters[p]
                case "R":
                    if command.Parameters["R"] != current_parameters["R"]:  # or line_M_funct != "":
                        line += " R" + str(command.Parameters["R"])
                        current_parameters["R"] = command.Parameters["R"]
                case "F":
                    if (
                        command.Parameters["F"] != current_parameters["F"]
                    ):  # or MACHINE_SKIP_PARAMS == False
                        if values["USE_FMAX"] and command.Parameters["F"] == FMAX_SPEED:
                            line += " F MAX"
                        else:
                            line += " F" + format_for_feed(
                                values, Units.Quantity(command.Parameters["F"], Units.Velocity)
                            )

                        current_parameters["F"] = command.Parameters["F"]
                case "S":
                    print("TODO: Handle 'S' parameter")
                case "M":  # NOTE: Currently not in use, as far as I know
                    point_line += str(command.Parameters["M"])

    # No parameter chane? No output
    if line == "L":
        return

    # # LBLIZE check and array creation
    # if LBLIZE_STAUS:
    #     i = H_Line_Params[0][MACHINE_WORK_AXIS]
    #     # to skip reposition movements rapid or not
    #     if MACHINE_LAST_POSITION[i] == H_Line_New[i] and line_rapid == False:
    #         HEIDEN_LBL_Get(MACHINE_LAST_POSITION, H_Line_New[i])
    #     else:
    #         HEIDEN_LBL_Get()

    gcode.append(line)


def output_arc(
    values: Values, gcode: Gcode, command: Command, current_parameters: ParseUtils.PathParameters
) -> None:
    # def output_arc(arc_Params, arc_direction, arc_comp, arc_feed, arc_rapid, arc_M_funct, Cmd_Number):
    """Output a circular movement."""

    print(f"Command: {command}")

    FMAX_SPEED = values["FMAX_SPEED"] / 60  # mm/min / 60 = mm/s
    # global FEED_MAX_SPEED
    # global COMPENSATION_DIFF_STATUS
    # global G_FUNCTION_STORE
    # global MACHINE_WORK_AXIS
    # global MACHINE_LAST_POSITION
    # global MACHINE_LAST_CENTER
    # global MACHINE_STORED_PARAMS
    # global MACHINE_SKIP_PARAMS
    # global MACHINE_USE_FMAX
    # Cmd_Number -= 1
    # H_ArcSameCenter = False
    # H_ArcIncr = ""
    center_line = "CC"
    point_line = "C"

    # Get command values
    if values["MOTION_MODE"] == "G91":  # Incremental
        # H_ArcIncr = "I"
        # for i in range(0, 3):
        #     a = H_Arc_Params[0][i]
        #     b = H_Arc_Params[2][i]
        #     # X Y Z
        #     if a in arc_Params:
        #         H_Arc_P_NEW[a] = arc_Params[a]
        #     else:
        #         H_Arc_P_NEW[a] = 0
        #     # I J K skip update for machine work axis
        #     if i != MACHINE_WORK_AXIS:
        #         if b in arc_Params:
        #             H_Arc_CC[a] = arc_Params[b]
        #         else:
        #             H_Arc_CC[a] = 0
        print("G2/3 incremental")
    else:  # Absolute
        for parameters in [["X", "I"], ["Y", "J"], ["Z", "K"]]:
            # Handle CC "X", "Y", "Z"; "I", "J", "K" parameters
            if parameters[1] in command.Parameters:
                current_parameters[parameters[1]] = (
                    current_parameters[parameters[0]] + command.Parameters[parameters[1]]
                )

            # Handle C "X", "Y", "Z"
            if parameters[0] in command.Parameters:
                current_parameters[parameters[0]] = command.Parameters[parameters[0]]

    # TODO: Select the right working axis of the machine
    # def Axis_Select(a, b, c, incr):
    #     if a in arc_Params and b in arc_Params:
    #         _H_ArcCenter = (
    #             incr + HEIDEN_Format(a, H_Arc_CC[a]) + " " + incr + HEIDEN_Format(b, H_Arc_CC[b])
    #         )
    #         if c in arc_Params and arc_Params[c] != MACHINE_LAST_POSITION[c]:
    #             # if there are 3 axis movements it need to be polar arc
    #             _H_ArcPoint = HEIDEN_PolarArc(
    #                 H_Arc_CC[a],
    #                 H_Arc_CC[b],
    #                 H_Arc_P_NEW[a],
    #                 H_Arc_P_NEW[b],
    #                 arc_Params[c],
    #                 c,
    #                 incr,
    #             )
    #         else:
    #             _H_ArcPoint = (
    #                 " "
    #                 + incr
    #                 + HEIDEN_Format(a, H_Arc_P_NEW[a])
    #                 + " "
    #                 + incr
    #                 + HEIDEN_Format(b, H_Arc_P_NEW[b])
    #             )
    #         return [_H_ArcCenter, _H_ArcPoint]
    #     else:
    #         return ["", ""]

    # # set the right work plane based on tool direction
    # if MACHINE_WORK_AXIS == 0:  # tool on X axis
    #     Axis_Result = Axis_Select("Y", "Z", "X", H_ArcIncr)
    # elif MACHINE_WORK_AXIS == 1:  # tool on Y axis
    #     Axis_Result = Axis_Select("X", "Z", "Y", H_ArcIncr)
    # elif MACHINE_WORK_AXIS == 2:  # tool on Z axis
    #     Axis_Result = Axis_Select("X", "Y", "Z", H_ArcIncr)
    # # and fill with values
    # H_ArcCenter += Axis_Result[0]
    # H_ArcPoint += Axis_Result[1]

    # Set the right arc direction
    match command.Name:
        case "G2":
            command.Parameters["D"] = " DR-"
        case "G3":
            command.Parameters["D"] = " DR+"

    # TODO: Implement tool radius compensation
    command.Parameters["R"] = "0"
    # if COMPENSATION_DIFF_STATUS[0]:  # Diff from compensated ad not compensated path
    #     if COMPENSATION_DIFF_STATUS[1]:  # skip if already compensated
    #         Cmd_Number -= 1  # align
    #         i = True
    #         for j in H_Arc_Params[0]:
    #             if j in STORED_COMPENSATED_OBJ[Cmd_Number].Parameters and j in arc_Params:
    #                 if STORED_COMPENSATED_OBJ[Cmd_Number].Parameters[j] != arc_Params[j]:
    #                     i = False
    #         if i == False:
    #             H_Arc_Params[1][0] = "R" + arc_comp
    #     # COMPENSATION_DIFF_STATUS[1] = False # we can skip this control if already in compensation
    #     else:
    #         H_Arc_Params[1][0] = "R" + arc_comp  # not used by now

    for p in values["PARAMETER_ORDER"]:
        if p in command.Parameters:
            match p:
                case "X" | "Y" | "Z":
                    # TODO: Select the right working axis, disabling Z
                    if p == "Z":
                        continue

                    if values["MOTION_MODE"] == "G91":  # Incremental
                        current_parameters[p] = 0
                        # H_Line_New[i] = 0
                        # if i in line_Params:
                        #     if line_Params[i] != 0 or line_M_funct != "":
                        #         H_Line += " I" + HEIDEN_Format(i, line_Params[i])  # print incremental
                        # # update to absolute position
                        # H_Line_New[i] = MACHINE_LAST_POSITION[i] + H_Line_New[i]
                    else:  # Absolute
                        point_line += (
                            " "
                            + p
                            + format_for_axis(
                                values, Units.Quantity(current_parameters[p], Units.Length)
                            )
                        )
                case "I" | "J" | "K":
                    # TODO: Select the right working axis, disabling K
                    if p == "K":
                        continue

                    match p:
                        case "I":
                            center_line += " X"
                        case "J":
                            center_line += " Y"
                        case "K":
                            center_line += " Z"
                    center_line += format_for_axis(
                        values, Units.Quantity(current_parameters[p], Units.Length)
                    )
                case "D":  # NOTE: Always add the direction for debugging
                    point_line += str(command.Parameters["D"])
                case "R":
                    if command.Parameters["R"] != current_parameters["R"]:
                        point_line += " R" + str(command.Parameters["R"])
                        current_parameters["R"] = command.Parameters["R"]
                case "F":
                    if command.Parameters["F"] != current_parameters["F"]:
                        if values["USE_FMAX"] and command.Parameters["F"] == FMAX_SPEED:
                            point_line += " F MAX"
                        else:
                            point_line += " F" + format_for_feed(
                                values, Units.Quantity(command.Parameters["F"], Units.Velocity)
                            )

                        current_parameters["F"] = command.Parameters["F"]
                case "M":  # NOTE: Currently not in use, as far as I know
                    point_line += str(command.Parameters["M"])

    # # check if we can skip CC print
    # if (
    #     MACHINE_LAST_CENTER["X"] == H_Arc_CC["X"]
    #     and MACHINE_LAST_CENTER["Y"] == H_Arc_CC["Y"]
    #     and MACHINE_LAST_CENTER["Z"] == H_Arc_CC["Z"]
    # ):
    #     H_ArcSameCenter = True

    # # LBLIZE check and array creation
    # if LBLIZE_STAUS:
    #     i = H_Arc_Params[0][MACHINE_WORK_AXIS]
    #     # to skip reposition movements
    #     if MACHINE_LAST_POSITION[i] == H_Arc_P_NEW[i]:
    #         if H_ArcSameCenter:
    #             HEIDEN_LBL_Get(MACHINE_LAST_POSITION, H_Arc_P_NEW[i])
    #         else:
    #             HEIDEN_LBL_Get(MACHINE_LAST_POSITION, H_Arc_P_NEW[i], 1)
    #     else:
    #         HEIDEN_LBL_Get()

    # Only output the center when it has changed
    if center_line != "CC":
        gcode.append(center_line)
        print(f"{center_line}")

    gcode.append(point_line)
    print(f"{point_line}")
    print(f"")


def output_optional_stop(values: Values, gcode: Gcode) -> None:
    """Output a optional stop of the machine."""

    if values["OUTPUT_COMMENTS"]:
        gcode.append(create_comment(values, "Optional stop"))

    # Preform the optional stop
    gcode.append("M1")


def output_forced_stop(values: Values, gcode: Gcode) -> None:
    """Output a forced stop of the machine."""

    if values["OUTPUT_COMMENTS"]:
        gcode.append(create_comment(values, "Forced stop"))

    # Preform the forced stop
    gcode.append("M2")


def output_tool_change(
    values: Values, gcode: Gcode, tc: Path.Tool.Controller.ToolController
) -> None:
    """Output a tool change."""

    if values["OUTPUT_COMMENTS"]:
        gcode.append(create_comment(values, "Begin toolchange"))

    if values["OUTPUT_TOOL_CHANGE"]:
        if values["STOP_SPINDLE_FOR_TOOL_CHANGE"] and values["spindle_state"] != "None":
            values["spindle_state"] = "None"
            gcode.append("M5")

        # Set the direction the spindle should turn in
        values["spindle_dir"] = tc.SpindleDir

        # TODO: Implement other working axis than Z
        gcode.append(
            f"TOOL CALL {tc.ToolNumber} Z S{format_for_spindle(values, Units.Quantity(tc.SpindleSpeed, Units.Length))}"
        )

        # Preform the TOOL CALL
        gcode.append("M6")

        # TODO: Should the spindle be started later?
        #       Probably before first G1 movement after toolchange
        match values["spindle_dir"]:
            case "Forward":
                gcode.append("M3")
            case "Reverse":
                gcode.append("M4")

        for line in values["TOOL_CHANGE"].splitlines(False):
            gcode.append(line)

    if values["OUTPUT_COMMENTS"]:
        gcode.append(create_comment(values, "End toolchange"))
