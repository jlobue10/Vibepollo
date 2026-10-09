# Driver rollout dependency from the 2026-10-09 audit

The initial audit found that this branch bundled `v0.1.0-beta.108`, source
`68724c4e6cae11e1508b6f835433ab55e5c3212d`. That package lacked the later
in-flight VHF teardown, haptic queue and per-controller keep-alive fixes.
Updating headers or a submodule alone could not update that driver binary.

## Package and consumer pin gap resolved

During follow-up, the repository owner published `v0.1.0-beta.109` and updated
Vibepollo's base in `88fbdecd485d2a5a4900a6db29fa6b3e5235497c`. This PR includes
that base update. The package contract, installer, workflow and gitlink agree:

- Source revision: `e6ca672f0429f7a84356eabb6e4659208ea9a9f4`.
- DriverVer: `10/09/2026,0.1.0.112`; protocol version: `2`.
- Archive: `libvirtualgamepad-0.1.0-beta.109-windows-x64.zip`.
- SHA-256: `411dc56baf9c8544b8624e238207c3cb613aafbd3181b631dd67f62d84ddbde1`.

The downloaded archive matches that digest, and all four manifest payload
hashes match their archive members. Its producer revision includes the audit
fixes and the concurrent `2a9bb08` correction preserving the full 4 ms cadence.
The real WDK build and unsigned-package check passed at the corrected source
in [run 37989614422](https://github.com/jlobue10/libvirtualgamepad/actions/runs/37989614422).
The subsequent tests/CI follow-up also passes both WDK/package verification
and four ASan/UBSan keep-alive regressions in
[run 37991924762](https://github.com/jlobue10/libvirtualgamepad/actions/runs/37991924762).

## Remaining integration validation

The producer archive deliberately uses `msi-request-signing`; downstream
catalog/setup signing and installation are still required. Archive hash and
manifest checks are not an installed-driver or signature validation result.
Before shipping, verify the actual installed driver revision, stress hot-unplug
under input/haptic load, and test two controllers with one busy and the other
idle. Preserve a tested rollback package.

The touch-ownership fix is independent of the producer update. The PR workflow
builds the full unsigned Windows server/MSI and runs the actual Windows VHF
policy tests. Check the current PR head's result; an older build against
`beta.108` does not validate the new package contract. This audit did not publish,
sign or install a release.
