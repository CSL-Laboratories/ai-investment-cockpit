#ifndef CSL_SANDBOX_ARGV_H
#define CSL_SANDBOX_ARGV_H
#include "sandbox_common.h"

/* F3a argv allowlist. Returns 0 if ok, -1 on reject (reason code only). */
int csl_argv_allow(int argc, char **argv);

#endif
