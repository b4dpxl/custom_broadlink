Custom extension to the standard Broadlink integration for Home Assistant to support the British General BG1 smart 
switches. E.g.:
- https://www.screwfix.com/p/british-general-smart-home-13a-2-gang-sp-smart-socket-white/827gv?_requestid=23673427gv?_requestid=236734
- https://www.screwfix.com/p/british-general-800-series-13a-2-gang-sp-smart-socket-white/306hv?_requestid=612158

The python-broadlink library has been extended to support these devices, however the updated version hasn't been 
released yet, so can't easily be updated in HA. Once a release is done, I'll create a PR for HA. In the meantime, you
can run this as a custom component.

Configuration is the same as standard broadlink, just set the "type" to `bg1`. It will create a "left" and "right" 
switch for the device.****