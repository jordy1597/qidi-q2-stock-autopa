# QIDI Q2 Stock AutoPA (experimental)

> **Experimental, personal field report — not an official QIDI or Klipper feature.**

This repository documents a stock-QIDI-Q2 workflow that reads the existing
CS1237 bed load cell through QIDI's probe_air path and uses it for
**manually initiated** pressure-advance sweeps.

It does not replace the QIDI firmware, display, RFID, homing, or QIDI Box
firmware. It is intentionally conservative:

- actual observed host sample rate is about **37–40 Hz**, not the ADC's internal
  1280 Hz setting;
- an unknown material never triggers heating, movement, loading, or calibration;
- known material classes only receive their stored PA;
- sweeps require a manual command, a safe Z height (at least 80 mm), and use the
  existing QIDI purge, park, and print-end paths;
- a Box return is attempted only after a successful sweep and a verified physical
  QIDI slot.

## What is included

- q2_loadcell.py — read-only adapter for QIDI's sensor helper;
- qpa_material_db.py — material-class PA database, QIDI material lookup, and
  manual QIDI Box calibration orchestration;
- qpa_controls.cfg — German Klipper/Fluidd macros and safety wrappers;
- docs/EXPERIENCE_REPORT_DE.md — full German field report: measurements,
  failures, safeguards, and current limitations.

## Important limitations

This is **not a plug-and-play installer**. QIDI firmware versions and Box
behavior vary; inspect and back up your own configuration before copying
anything. Do not run it while printing. Verify every new material with a small
test print.

Flow / flow ratio remains intentionally separate: the load cell measures nozzle
pressure, not deposited wall geometry.

The code base does not bundle G0BL1N/autopa. Obtain it from its upstream project
and follow its license:
https://github.com/G0BL1N/autopa

## References

- [Klipper Pressure Advance guide](https://github.com/Klipper3d/klipper/blob/master/docs/Pressure_Advance.md)
- [Klipper Load Cell documentation](https://github.com/Klipper3d/klipper/blob/master/docs/Load_Cell.md)
- [QIDI Q2 firmware sources](https://github.com/QIDITECH/QIDI_Q2)


