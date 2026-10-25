#ifndef CSL_SANDBOX_ARGV_H
#define CSL_SANDBOX_ARGV_H
#include "sandbox_common.h"

/* F3a argv allowlist. Returns 0 if ok, -1 on reject (reason code only). */
int csl_argv_allow(int argc, char **argv);

/* Same parser as allow. Drops exactly the tokens allow classified as
 * incoming MCP (flag and its value slot), then appends the launcher-owned
 * empty MCP pair. out[cap] is the array bound including NULL. */
int csl_argv_rewrite(int argc, char **argv, char **out, int cap, int *outn);

#endif
