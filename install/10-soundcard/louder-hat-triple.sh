#!/usr/bin/env bash
# install/10-soundcard/louder-hat-triple.sh
# Custom 3-DAC setup for the Lounge: 2× Plus 2X + 1× non-Plus 1X stacked.
#
# TODO: This configuration requires a custom device-tree overlay that
# Sonocotta has not yet shipped. Once the overlay is available:
#   1. drop it into /boot/firmware/overlays/
#   2. replace the `dtoverlay=…` line below with the correct name + params
#   3. add the third chip's amixer numid block (cset numid=41+ etc.)
#
# Stacking rules confirmed with Andrey (Sonocotta):
#   - Plus boards have fixed I2C addresses and cannot stack with each other
#   - A non-Plus sits in a separate address range and CAN stack on a Plus
#   - Net result: two Plus 2X boards need separate I2C bus allocation (TBD)

source "$(dirname "$0")/../_lib.sh"

log_warn "louder-hat-triple is not yet implemented."
log_warn "The Lounge build is blocked on a custom Sonocotta DT overlay."
log_warn "Once available, edit $(readlink -f "$0") to enable installation."

# Uncomment and adjust when ready:
# PRIMARY="$(pq soundcard.primary_i2c)"
# SECONDARY="$(pq soundcard.secondary_i2c)"
# TERTIARY="$(pq soundcard.tertiary_i2c)"
# ensure_line_in_config_txt "dtoverlay=tas58xx-triple,i2creg_1=$PRIMARY,i2creg_2=$SECONDARY,i2creg_3=$TERTIARY"
# ensure_line_in_config_txt "dtoverlay=snd-aloop"

exit 0  # don't fail the overall install — base system can still come up
