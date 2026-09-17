# QIDI Q2 CS1237 read-only load-cell adapter.
#
# This module intentionally uses only the vendor probe_air object's documented
# sensor_helper.read_origin_data() path.  It does not move axes, heat, extrude,
# alter probe settings, or send commands to the QIDI Box.
import statistics


class _SensorInfo:
    def get_samples_per_second(self):
        return 40.0


class _Collector:
    """Small subset of Klipper's load_cell collector contract for AutoPA."""
    def __init__(self, owner):
        self.owner = owner
        self.is_started = False
        self.samples = []
        self.errors = []
        self.min_time = None
        self.timer = owner.reactor.register_timer(
            self._sample, owner.reactor.NEVER)

    def start_collecting(self, min_time=None):
        self.samples = []
        self.errors = []
        self.min_time = min_time
        self.is_started = True
        self.owner.reactor.update_timer(self.timer, self.owner.reactor.NOW)

    def _sample(self, eventtime):
        if not self.is_started:
            return self.owner.reactor.NEVER
        try:
            raw = int(self.owner._read_raw())
            print_time = self.owner._print_time(eventtime)
            if self.min_time is None or print_time >= self.min_time:
                self.samples.append([print_time, 0.0, raw, self.owner.tare])
        except Exception as exc:
            self.errors.append(str(exc))
        return eventtime + self.owner.sample_period

    def collect_until(self, print_time):
        # Yield the reactor until the printer's timebase reaches the final
        # queued move; the timer above continues sampling during that period.
        while self.is_started and self.owner._print_time(
                self.owner.reactor.monotonic()) < print_time:
            self.owner.reactor.pause(self.owner.reactor.monotonic() + 0.050)
        self.is_started = False
        self.owner.reactor.update_timer(self.timer, self.owner.reactor.NEVER)
        return self.samples, self.errors


class Q2LoadCell:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = 'q2_loadcell'
        self.sample_period = 1.0 / 40.0
        self.tare = 0.0
        self.sensor = _SensorInfo()
        self.collector = _Collector(self)
        self.mcu = None
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.gcode.register_command(
            'QPA_SENSOR_TEST', self.cmd_QPA_SENSOR_TEST,
            desc='Read the QIDI Q2 CS1237 load cell without moving the printer')

    def _handle_ready(self):
        self.mcu = self.printer.lookup_object('mcu')
        # This is the standard discovery event used by G0BL1N/autopa.
        self.printer.send_event('load_cell:tare', self)

    def _read_raw(self):
        return self._get_sensor_direct().read_origin_data()

    def _get_sensor_direct(self):
        probe_air = self.printer.lookup_object('probe_air')
        return probe_air.sensor_helper

    def _print_time(self, eventtime):
        if self.mcu is None:
            self.mcu = self.printer.lookup_object('mcu')
        return self.mcu.estimated_print_time(eventtime)

    def get_collector(self):
        return self.collector

    def _get_sensor(self, gcmd):
        try:
            probe_air = self.printer.lookup_object('probe_air')
        except Exception as exc:
            raise gcmd.error('q2_loadcell: [probe_air] is unavailable: %s' % (exc,))
        sensor = getattr(probe_air, 'sensor_helper', None)
        if sensor is None or not hasattr(sensor, 'read_origin_data'):
            raise gcmd.error(
                'q2_loadcell: probe_air.sensor_helper.read_origin_data() is unavailable')
        return sensor

    def cmd_QPA_SENSOR_TEST(self, gcmd):
        duration = gcmd.get_float('DURATION', 5.0, minval=0.5, maxval=30.0)
        sensor = self._get_sensor(gcmd)
        sample_period = 1.0 / 40.0
        values = []
        read_errors = 0
        start = self.reactor.monotonic()
        deadline = start + duration
        next_sample = start

        # gcode handlers run as reactor greenlets.  pause() yields between
        # polls, so this preserves normal Klipper scheduling while sampling.
        while self.reactor.monotonic() < deadline:
            try:
                values.append(int(sensor.read_origin_data()))
            except Exception:
                read_errors += 1

            next_sample += sample_period
            now = self.reactor.monotonic()
            if next_sample < now:
                next_sample = now
            self.reactor.pause(next_sample)

        elapsed = self.reactor.monotonic() - start
        count = len(values)
        rate = count / elapsed if elapsed > 0.0 else 0.0
        if count:
            tare = statistics.median(values)
            self.tare = tare
            sigma = statistics.pstdev(values) if count > 1 else 0.0
            tare_text = '%.1f counts' % (tare,)
            sigma_text = '%.2f counts' % (sigma,)
        else:
            tare_text = 'n/a'
            sigma_text = 'n/a'

        passed = rate >= 35.0 and read_errors == 0
        gcmd.respond_info(
            'q2_loadcell sensor test: %s\n'
            'duration=%.3fs samples=%d effective_rate=%.2fHz\n'
            'median_tare=%s noise_sigma=%s read_errors=%d' % (
                'PASS' if passed else 'FAIL', duration, count, rate,
                tare_text, sigma_text, read_errors))
        if not passed:
            raise gcmd.error(
                'q2_loadcell sensor test failed (requires >=35Hz and read_errors=0)')


def load_config(config):
    return Q2LoadCell(config)

