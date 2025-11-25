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
from typing import Any, Dict

import Path.Base.Util as PathUtil
import Path.Post.Utils as PostUtils
import Path.Tool.Controller as PathToolController
from Path.Post.Processor import (
    GCodeOrNone,
    Postables,
    PostProcessor,
    GCodeSections,
    Section,
    Sublist,
)
from Path.Post.UtilsExport import Gcode, Values, finalize_export
from Path.Post.UtilsParse import format_command_line
from PathScripts.PathUtils import findParentJob

import Path
import FreeCAD

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
# *      Heidenhain Klartext example      *
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
    """The Refactored Heidenhain post processor class."""

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

        values["PGM_UPPERCASE"] = True

        values["COMMENT_PREFIX"] = "; ("
        values["COMMENT_POSTFIX"] = ")"

        values["LIST_TOOLS_IN_PREAMBLE"] = True

        values["ENABLE_COOLANT"] = True
        values["SUPPORT_FLOOD_COOLANT"] = True
        values["SUPPORT_MIST_COOLANT"] = False

        # #
        # # The order of parameters.
        # #
        # values["PARAMETER_ORDER"] = [
        #     "X",
        #     "Y",
        #     "Z",
        #     "A",
        #     "B",
        #     "C",
        #     "I",
        #     "J",
        #     "F",
        #     "S",
        #     "T",
        #     "Q",
        #     "R",
        #     "L",
        #     "H",
        #     "D",
        #     "P",
        # ]

        #
        # Used in the argparser code as the "name" of the postprocessor program.
        #
        values["MACHINE_NAME"] = "Heidenhain"

        values["line_number"] = 0
        values["LINE_INCREMENT"] = 1
        values["LINE_PREFIX"] = ""
        values["LINE_POSTFIX"] = "  "

        values["POSTPROCESSOR_FILE_NAME"] = __name__

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


def export(values: Values, objectslist, filename: str) -> str:
    """Custom export function for exporting Heidenhain Klartext"""
    coolant_mode: str = "None"
    gcode: Gcode = []

    for obj in objectslist:
        if not hasattr(obj, "Path"):
            print(f"The object {obj.Name} is not a path.")
            print("Please select only path and Compounds.")
            return ""

    # Find the current job all opperations belong to, index 0 contains the Fixture
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

    for obj in objectslist:
        # Skip inactive operations
        if not PathUtil.activeForOp(obj):
            continue

        # TODO: Check if previous coolant mode is still relevant and coolant does
        #       not need to be turned off in between opperations
        new_coolant_mode = PathUtil.coolantModeForOp(obj)

        output_start_bcnc(values, gcode, obj)
        output_preop(values, gcode, obj)

        if coolant_mode != new_coolant_mode:
            coolant_mode = new_coolant_mode
            output_coolant_update(values, gcode, coolant_mode)

        # output the G-code for the group (compound) or simple path
        # parse_a_group(values, gcode, obj)
        output_postop(values, gcode, obj)
        # output_coolant_update(values, gcode, "None") # TODO: Temp turn coolant off after every operation

    output_return_to(values, gcode)
    # output_end_bcnc(values, gcode)
    output_pgm_end(values, gcode, current_job)

    output_postamble_header(values, gcode)
    output_tool_return(values, gcode)
    output_safetyblock(values, gcode)
    output_postamble(values, gcode)

    add_linenumbers(values, gcode)

    finalize_export(values, gcode, filename)


# ********************************************************
# * Exporting Klartext requires rewriting all of this... *
# ********************************************************


def output_pgm_begin(values: Values, gcode: Gcode, current_job) -> None:
    gcode.append(
        f"BEGIN PGM " + job_name(values, current_job) + " " + values["UNIT_FORMAT"].upper()
    )


def output_pgm_end(values: Values, gcode: Gcode, current_job) -> None:
    gcode.append(f"END PGM " + job_name(values, current_job) + " " + values["UNIT_FORMAT"].upper())


def job_name(values: Values, current_job) -> str:
    program_name: str = "NEW"

    if hasattr(current_job, "Label"):
        program_name = current_job.Label

    if values["PGM_UPPERCASE"]:
        program_name = program_name.upper()

    return program_name


def check_canned_cycles(values: Values) -> None:
    """Check canned cycles for drilling."""
    if values["TRANSLATE_DRILL_CYCLES"]:
        if len(values["SUPPRESS_COMMANDS"]) == 0:
            values["SUPPRESS_COMMANDS"] = ["G99", "G98", "G80"]
        else:
            values["SUPPRESS_COMMANDS"] += ["G99", "G98", "G80"]


# def output_coolant_off(values: Values, gcode: Gcode, coolant_mode: str) -> None:
#     """Output the commands to turn coolant off if necessary."""
#     comment: str

#     if values["ENABLE_COOLANT"] and coolant_mode != "None":
#         if values["OUTPUT_COMMENTS"]:
#             comment = create_comment(values, f"Coolant Off: {coolant_mode}")
#             gcode.append(f"{comment}")
#         gcode.append("M9")


def output_coolant_update(values: Values, gcode: Gcode, coolant_mode: str) -> None:
    """Output the commands to turn coolant on if necessary."""
    used_coolant_mode: str = None
    command: str = None

    if values["ENABLE_COOLANT"]:
        match (coolant_mode, values["SUPPORT_FLOOD_COOLANT"], values["SUPPORT_MIST_COOLANT"]):
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
            if coolant_mode == "None":
                comment = "Coolant Off"
            elif coolant_mode == used_coolant_mode:
                comment = f"Coolant On: {coolant_mode}"
            else:
                comment = f"Coolant On: {coolant_mode} not available, using {used_coolant_mode}"
            gcode.append(create_comment(values, comment))

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


def output_return_to(values: Values, gcode: Gcode) -> None:
    """Output the RETURN_TO command."""
    cmd: str
    num_x: str
    num_y: str
    num_z: str

    if values["RETURN_TO"]:
        num_x = values["RETURN_TO"][0]
        num_y = values["RETURN_TO"][1]
        num_z = values["RETURN_TO"][2]
        cmd = format_command_line(values, ["G0", f"X{num_x}", f"Y{num_y}", f"Z{num_z}"])
        gcode.append(f"{cmd}")


def output_safetyblock(values: Values, gcode: Gcode) -> None:
    """Output the safety block."""
    line: str

    for line in values["SAFETYBLOCK"].splitlines(False):
        gcode.append(f"{line}")


def output_start_bcnc(values: Values, gcode: Gcode, obj) -> None:
    """Output the starting BCNC header."""
    comment: str

    if values["OUTPUT_BCNC"]:
        comment = create_comment(values, f"Block-name: {obj.Label}")
        gcode.append(f"{comment}")
        comment = create_comment(values, "Block-expand: 0")
        gcode.append(f"{comment}")
        comment = create_comment(values, "Block-enable: 1")
        gcode.append(f"{comment}")


def output_tool_list(values: Values, gcode: Gcode, objectslist) -> None:
    """Output a list of the tools used in the objects."""
    comment: str

    if values["OUTPUT_COMMENTS"] and values["LIST_TOOLS_IN_PREAMBLE"]:
        for item in objectslist:
            if hasattr(item, "Proxy") and isinstance(item.Proxy, PathToolController.ToolController):
                comment = create_comment(values, f"T{item.ToolNumber}={item.Name}")
                gcode.append(f"{comment}")


def output_tool_return(values: Values, gcode: Gcode) -> None:
    """Output the tool return block."""
    line: str

    for line in values["TOOLRETURN"].splitlines(False):
        gcode.append(f"{line}")


def output_units(values: Values, gcode: Gcode) -> None:
    """Verify if PREAMBLE or SAFETYBLOCK have changed UNITS."""

    if "G21" in values["PREAMBLE"] or "G21" in values["SAFETYBLOCK"]:
        values["UNITS"] = "G21"
        values["UNIT_FORMAT"] = "mm"
        values["UNIT_SPEED_FORMAT"] = "mm/min"
    elif "G20" in values["PREAMBLE"] or "G20" in values["SAFETYBLOCK"]:
        values["UNITS"] = "G20"
        values["UNIT_FORMAT"] = "in"
        values["UNIT_SPEED_FORMAT"] = "in/min"
    else:
        gcode.append(f'{values["UNITS"]}')


def add_linenumbers(values: Values, gcode: Gcode) -> None:
    """Add linenumbers to every line of gcode."""

    if not values["OUTPUT_LINE_NUMBERS"]:
        return ""

    for i, line in enumerate(gcode):
        line_num = str(values["line_number"])
        values["line_number"] += values["LINE_INCREMENT"]
        gcode[i] = values["LINE_PREFIX"] + line_num + values["LINE_POSTFIX"] + line


def create_comment(values: Values, comment_string: str) -> str:
    """Create a comment from a string using the correct pre and postfixes."""

    return values["COMMENT_PREFIX"] + comment_string + values["COMMENT_POSTFIX"]
