# normalize/stderr-rules.sed
#
# Sed script applied to flpdf-cli stderr by shim/qpdf when
# FLPDF_QTEST_NORMALIZE points at this file. The goal is to absorb purely
# cosmetic differences between flpdf and qpdf diagnostic output so that
# qtest expectations written for qpdf can still match flpdf's behaviour
# without patching the vendored .test files.
#
# Keep rules SMALL and ORTHOGONAL. If a normalization is semantic (changes
# meaning, not just prefix/wording) it does not belong here — encode the
# divergence in allowlist.txt instead.

# Prefix replacement: "flpdf:" → "qpdf:"
s/^flpdf:/qpdf:/

# Additional rules will be added as concrete .test failures motivate them.
