# Optional critical-battery action

UPower may resolve its critical action to sleep where hibernation is absent;
s2idle continues to consume battery. `50-p16-critical.conf` is the MIT drop-in
from Johann Hartmann's PR4 commit `40a88e842e7dd9616b6892dd875635c1e21b9c59`.
It sets only CriticalPowerAction=PowerOff, not battery action thresholds or a
charge limit. This system-wide policy also affects UPower-managed UPS devices.

If desired, review and manually install the file through your distribution's
UPower configuration override mechanism (`/etc/UPower/UPower.conf.d/` on the
validated UPower), restart UPower deliberately, then run
`python3 verify-critical-action.py`. It reads the daemon's GetCriticalAction
D-Bus method and succeeds only for PowerOff. No power action is triggered.
Run `python3 test_verify_critical_action.py` in this directory. The verifier
tests use a simulated busctl and check the complete D-Bus command. Package installation and service
restart are intentionally not automated here. Local running-daemon verification
succeeded; battery depletion/forced shutdown was not tested.
