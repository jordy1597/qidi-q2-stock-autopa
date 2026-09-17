# Material-class PA database for a stock QIDI Q2.
# QIDI Box actions are exposed only through explicit manual commands.  Automatic
# material lookup may apply a known stored value, but never starts calibration
# or triggers filament motion by itself.
import configparser
import os
import re


class QpaMaterialDb:
    _material_re = re.compile(r'^[A-Z0-9][A-Z0-9_-]*$')

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.db = {}
        self.current_material = None
        self.current_slot = None
        self.current_qidi_name = None
        self.qidi_materials = {}
        self._active_signature = None
        self._poll_timer = self.reactor.register_timer(
            self._poll_qidi_material, self.reactor.NEVER)
        self.qidi_filament_list = os.path.expanduser(config.get(
            'qidi_filament_list',
            '~/printer_data/config/officiall_filas_list.cfg'))
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.gcode.register_command('QPA_LIST', self.cmd_QPA_LIST,
                                    desc='List stored PA values by material class')
        self.gcode.register_command('QPA_SHOW_CURRENT', self.cmd_QPA_SHOW_CURRENT,
                                    desc='Show the current session PA value')
        self.gcode.register_command('QPA_SET', self.cmd_QPA_SET,
                                    desc='Store and apply PA: MATERIAL= PA=')
        self.gcode.register_command('QPA_FORGET', self.cmd_QPA_FORGET,
                                    desc='Remove stored PA: MATERIAL=')
        self.gcode.register_command('QPA_APPLY', self.cmd_QPA_APPLY,
                                    desc='Apply stored PA only: MATERIAL=')
        self.gcode.register_command('QPA_MATERIAL_LOADED',
                                    self.cmd_QPA_MATERIAL_LOADED,
                                    desc='Successful load hook: MATERIAL=')
        self.gcode.register_command('QPA_CALIBRATE', self.cmd_QPA_CALIBRATE,
                                    desc='Manually sweep a previously unknown material')
        self.gcode.register_command('QPA_RECALIBRATE', self.cmd_QPA_RECALIBRATE,
                                    desc='Manually re-sweep and replace a material PA')
        self.gcode.register_command('QPA_BOX_RECALIBRATE',
                                    self.cmd_QPA_BOX_RECALIBRATE,
                                    desc='Load, verify, calibrate, and return a QIDI Box slot')
        self.gcode.register_command('QPA_RETURN_STATUS',
                                    self.cmd_QPA_RETURN_STATUS,
                                    desc='Report whether a QIDI Box return is safe; no motion')

    def _handle_ready(self):
        variables = self.printer.lookup_object('save_variables')
        saved = getattr(variables, 'allVariables', {}).get('qpa_material_pa', {})
        if isinstance(saved, dict):
            for material, value in saved.items():
                try:
                    name = self._normalize_material(str(material))
                    pa = float(value)
                    if pa >= 0.0:
                        self.db[name] = pa
                except Exception:
                    pass
        # Stock QIDI's G-code parameter parser removes quote characters from a
        # dictionary literal.  New records therefore use ASCII-code lists; the
        # format contains only digits and punctuation and survives that parser.
        elif isinstance(saved, list):
            for item in saved:
                try:
                    codes, value = item
                    name = self._normalize_material(
                        ''.join(chr(int(code)) for code in codes))
                    pa = float(value)
                    if pa >= 0.0:
                        self.db[name] = pa
                except Exception:
                    pass
        self._load_qidi_materials()
        # Check the already active QIDI selection once after startup, then only
        # react when QIDI changes its own active slot/material selection.
        self._refresh_qidi_material(initial=True)
        self.reactor.update_timer(self._poll_timer, self.reactor.monotonic() + 1.)

    def _load_qidi_materials(self):
        parser = configparser.ConfigParser()
        try:
            parser.read(self.qidi_filament_list)
            for section in parser.sections():
                match = re.match(r'^fila(\d+)$', section, re.I)
                if not match:
                    continue
                name = parser.get(section, 'filament', fallback='').strip()
                material = parser.get(section, 'type', fallback='').strip()
                if name and material:
                    self.qidi_materials[int(match.group(1))] = (name, material)
        except Exception:
            self.qidi_materials = {}

    def _qidi_selected_slot(self):
        """Return QIDI's unambiguous currently selected physical slot."""
        try:
            controller = self.printer.lookup_object('multi_color_controller')
            status = controller.get_status(self.reactor.monotonic())
            states = status.get('slots', {}).get('states', {})
            slots = [name for name, state in states.items()
                     if int(state) == 3 and re.match(r'^slot(?:[0-9]|1[0-6])$', name)]
            return slots[0] if len(slots) == 1 else None
        except Exception:
            return None

    def _qidi_return_slot_status(self):
        """Return (slot, reason) only for a strictly verified loaded QIDI slot.

        This is deliberately stricter than material lookup.  It is used by a
        read-only diagnostic before any future automatic Box return.
        """
        try:
            controller = self.printer.lookup_object('multi_color_controller')
            status = controller.get_status(self.reactor.monotonic())
            variables = self.printer.lookup_object('save_variables').allVariables
            extruder = status.get('extruder', {})
            if not extruder.get('loaded') or not extruder.get('filament_detected'):
                return None, 'the QIDI extruder reports no loaded filament'
            reported = str(status.get('slots', {}).get('last_loaded', '')).strip()
            cached = str(variables.get('last_load_slot', '')).strip()
            if not re.match(r'^slot(?:[0-9]|1[0-6])$', reported):
                return None, 'QIDI has no valid last_loaded physical slot'
            if reported != cached:
                return None, 'QIDI slot sources disagree (%s vs %s)' % (reported, cached)
            state = status.get('slots', {}).get('states', {}).get(reported)
            if int(state) not in (2, 3):
                return None, 'slot %s is not in a loaded/selected state (%s)' % (reported, state)
            return reported, None
        except Exception as exc:
            return None, 'unable to verify QIDI slot (%s)' % exc

    @staticmethod
    def _qidi_slot_label(slot):
        match = re.match(r'^slot(\d+)$', slot)
        if not match:
            return slot
        index = int(match.group(1))
        return '%d%s' % (index // 4 + 1, chr(ord('A') + index % 4))

    def _qidi_active_material(self):
        variables = self.printer.lookup_object('save_variables').allVariables
        # The controller's selected physical slot is authoritative.  The
        # legacy last_load_slot cache can temporarily point at slot16/Rack.
        slot = self._qidi_selected_slot() or str(variables.get('last_load_slot', '')).strip()
        match = re.match(r'^slot(\d+)$', slot)
        if not match:
            return None
        index = variables.get('filament_slot%s' % match.group(1))
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        entry = self.qidi_materials.get(index)
        if entry is None:
            return (slot, index, None, None)
        name, material = entry
        if re.match(r'^PLA\s+(MATTE|MATT)\b', name, re.I):
            material = 'PLA-MATTE'
        return (slot, index, name, self._normalize_material(material))

    def _refresh_qidi_material(self, initial=False):
        active = self._qidi_active_material()
        if active is None:
            return
        slot, index, name, material = active
        signature = (slot, index)
        if not initial and signature == self._active_signature:
            return
        self._active_signature = signature
        self.current_slot = slot
        self.current_qidi_name = name
        if material is None:
            self.gcode.respond_info(
                'qpa: QIDI %s uses unknown material selection %s; no calibration was started'
                % (slot, index))
            return
        # Only set a known, persisted value.  This path has no calibration,
        # heating, movement, extrusion, QIDI-Box or RFID side effects.
        self.current_material = material
        pa = self.db.get(material)
        if pa is None:
            self.gcode.respond_info(
                'qpa: QIDI %s selected %s (%s); no stored PA and no calibration was started'
                % (slot, name, material))
            return
        self._apply(pa)
        self.gcode.respond_info(
            'qpa: QIDI %s selected %s -> %s; applied %.4f'
            % (slot, name, material, pa))

    def _poll_qidi_material(self, eventtime):
        try:
            self._refresh_qidi_material()
        except Exception:
            pass
        return eventtime + 1.

    def _normalize_material(self, material):
        name = material.strip().upper()
        if not self._material_re.match(name):
            raise ValueError('MATERIAL must use letters, digits, _ or -')
        return name

    def _material_from_gcmd(self, gcmd):
        try:
            return self._normalize_material(gcmd.get('MATERIAL'))
        except Exception as exc:
            raise gcmd.error('qpa: invalid MATERIAL: %s' % (exc,))

    def _save_db(self):
        # Values contain no quotes or whitespace, so the QIDI parser passes
        # this literal unchanged to save_variables' ast.literal_eval().
        encoded = '[' + ','.join(
            '[[%s],%.6g]' % (','.join(str(ord(char)) for char in key),
                              self.db[key]) for key in sorted(self.db)) + ']'
        self.gcode.run_script_from_command(
            'SAVE_VARIABLE VARIABLE=qpa_material_pa VALUE=%s' % encoded)

    def _apply(self, pa):
        self.gcode.run_script_from_command(
            'SET_PRESSURE_ADVANCE ADVANCE=%.6f' % pa)

    def _store_and_apply(self, material, pa):
        self.db[material] = pa
        self.current_material = material
        self._save_db()
        self._apply(pa)

    def cmd_QPA_LIST(self, gcmd):
        if not self.db:
            gcmd.respond_info('qpa: no material PA values stored')
            return
        lines = ['qpa material PA database:']
        lines.extend('%-10s %.4f' % (name, self.db[name])
                     for name in sorted(self.db))
        gcmd.respond_info('\n'.join(lines))

    def cmd_QPA_SHOW_CURRENT(self, gcmd):
        extruder = self.printer.lookup_object('toolhead').get_extruder()
        status = extruder.get_status(self.reactor.monotonic())
        suffix = (' material=%s' % self.current_material
                  if self.current_material else '')
        if self.current_slot:
            suffix += ' slot=%s' % self.current_slot
        if self.current_qidi_name:
            suffix += ' qidi_name=%s' % self.current_qidi_name
        gcmd.respond_info('qpa: session pressure_advance=%.4f%s' % (
            status.get('pressure_advance', 0.0), suffix))

    def cmd_QPA_RETURN_STATUS(self, gcmd):
        slot, reason = self._qidi_return_slot_status()
        if slot:
            gcmd.respond_info(
                'qpa: return check OK; a future successful manual calibration '
                'could return to QIDI %s (%s); no movement was made' %
                (self._qidi_slot_label(slot), slot))
        else:
            gcmd.respond_info(
                'qpa: return check NOT armed; no movement was made: %s' % reason)

    def cmd_QPA_SET(self, gcmd):
        material = self._material_from_gcmd(gcmd)
        pa = gcmd.get_float('PA', minval=0.0, maxval=1.0)
        self._store_and_apply(material, pa)
        gcmd.respond_info('qpa: stored and applied %s -> %.4f' % (material, pa))

    def cmd_QPA_FORGET(self, gcmd):
        material = self._material_from_gcmd(gcmd)
        if material not in self.db:
            raise gcmd.error('qpa: no stored PA for %s' % material)
        del self.db[material]
        if self.current_material == material:
            self.current_material = None
        self._save_db()
        gcmd.respond_info('qpa: forgot %s (current session PA unchanged)' % material)

    def _apply_known_or_report(self, gcmd, material):
        pa = self.db.get(material)
        if pa is None:
            # Deliberately informational: this path must never fall through to
            # calibration, heat, motion, extrusion, or QIDI-Box commands.
            gcmd.respond_info(
                'qpa: no stored PA for %s; no calibration was started' % material)
            return False
        self.current_material = material
        self._apply(pa)
        gcmd.respond_info('qpa: applied %s -> %.4f' % (material, pa))
        return True

    def cmd_QPA_APPLY(self, gcmd):
        self._apply_known_or_report(gcmd, self._material_from_gcmd(gcmd))

    def cmd_QPA_MATERIAL_LOADED(self, gcmd):
        # Intended to be called only *after* an existing load path succeeds.
        # It has no QIDI Box/RFID/filament-motion side effects of its own.
        self._apply_known_or_report(gcmd, self._material_from_gcmd(gcmd))

    def _manual_calibration(self, gcmd, replace, expected_slot=None):
        material = self._material_from_gcmd(gcmd)
        if material in self.db and not replace:
            raise gcmd.error('qpa: %s already exists; use QPA_RECALIBRATE' % material)
        hotend = gcmd.get_int('HOTEND', minval=160, maxval=350)
        purge = gcmd.get_int('PURGE', 1, minval=0, maxval=1)
        # Snapshot a strictly verified physical QIDI slot before any motion.
        # It is used only after a successful measurement; failed/aborted runs
        # never cut or unload filament.
        return_slot, return_reason = self._qidi_return_slot_status()
        if expected_slot is not None and return_slot != expected_slot:
            raise gcmd.error(
                'qpa: requested Box slot %s was not verified after loading (%s)'
                % (expected_slot, return_reason or 'different slot reported'))
        prepared = False
        successful = False
        try:
            self.gcode.run_script_from_command(
                'QPA_SAFE_PREPARE HOTEND=%d PURGE=%d' % (hotend, purge))
            prepared = True
            self.gcode.run_script_from_command('AUTOPA_SWEEP APPLY=0 WOBBLE=0.5 VFR=12 VFR_LOW=2 TSLOW=0.8 TFAST=0.5 CYCLES=5 KSTART=0.02 KEND=0.06 KSTEP=0.0025 WARMUP=2 PRIME=10 RETRACT=3 MAXFILAMENT=300')
            autopa = self.printer.lookup_object('autopa')
            result = getattr(autopa, '_last', {}).get('sweep', {})
            pa = result.get('k_opt')
            if pa is None or float(pa) < 0.0:
                raise gcmd.error('qpa: sweep produced no valid PA; nothing saved')
            self._store_and_apply(material, float(pa))
            successful = True
            gcmd.respond_info('qpa: calibration saved and applied %s -> %.4f' %
                              (material, float(pa)))
        finally:
            if prepared:
                cleanup = 'QPA_SAFE_CLEANUP'
                if successful and return_slot:
                    cleanup += ' RETURN_SLOT=%s' % return_slot
                elif successful:
                    gcmd.respond_info(
                        'qpa: Box return not armed; filament remains loaded: %s' %
                        return_reason)
                self.gcode.run_script_from_command(cleanup)

    def cmd_QPA_BOX_RECALIBRATE(self, gcmd):
        # This is manual-only.  Use QIDI's stock loader, then require its
        # controller and the filament sensor to confirm the exact requested slot
        # before any heating, purge, or sweep begins.
        slot = gcmd.get('SLOT', '').strip().lower()
        if not re.match(r'^slot(?:[0-9]|1[0-6])$', slot):
            raise gcmd.error('qpa: SLOT must be a QIDI physical slot, e.g. slot0')
        stats = self.printer.lookup_object('print_stats').get_status(
            self.reactor.monotonic())
        if stats.get('state') not in ('standby', 'complete', 'cancelled', 'error'):
            raise gcmd.error('qpa: Box calibration is only allowed while idle')
        # Validate supplied manual material and temperature before Box motion.
        self._material_from_gcmd(gcmd)
        hotend = gcmd.get_int('HOTEND', minval=160, maxval=350)
        # QIDI's stock Box loader extrudes; heat and wait before invoking it.
        gcmd.respond_info('qpa: heating to %d C before loading %s' % (hotend, slot))
        self.gcode.run_script_from_command('M109 S%d' % hotend)
        self.gcode.run_script_from_command('EXTRUDER_LOAD SLOT=%s' % slot)
        self._manual_calibration(gcmd, replace=True, expected_slot=slot)

    def cmd_QPA_CALIBRATE(self, gcmd):
        self._manual_calibration(gcmd, replace=False)

    def cmd_QPA_RECALIBRATE(self, gcmd):
        self._manual_calibration(gcmd, replace=True)


def load_config(config):
    return QpaMaterialDb(config)

