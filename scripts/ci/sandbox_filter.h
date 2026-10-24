#ifndef CSL_SANDBOX_FILTER_H
#define CSL_SANDBOX_FILTER_H
#include "sandbox_common.h"

#define CSL_STATUS_OBSERVED 1
#define CSL_STATUS_NOT_OBSERVED 2
#define CSL_STATUS_CONFLICT 3

typedef struct {
    int status;
    unsigned tokens; /* bit0 network, bit1 filesystem, bit2 env, bit3 seccomp */
    int positive;
} csl_filter_result;

/* Pad to CAP_BYTES with NULs, then split on \\n. Trailing NULs are not part of
 * a last unterminated line (L-c). Reconstruct from constants only. */
csl_filter_result csl_filter_capture(const unsigned char *data, size_t n);
void csl_reconstruct(unsigned tokens, char *out, size_t cap);

#endif
