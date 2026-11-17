// Block coverage for choosing generated input tapes. A library built with
// -fsanitize-coverage=trace-pc calls __sanitizer_cov_trace_pc at every basic
// block; this marks the block's address, as an offset into the library, in a
// byte map that tools/validation/tapes.py reads and clears between tapes.
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum { MSL_TRACE_BYTES = 96 << 20 };

extern const char __ehdr_start[];
static uint8_t trace_map[MSL_TRACE_BYTES];

__attribute__((visibility("default"))) void __sanitizer_cov_trace_pc(void)
{
    size_t offset = (size_t) ((const char*) __builtin_return_address(0) - __ehdr_start);
    if (offset < MSL_TRACE_BYTES) {
        trace_map[offset] = 1;
    }
}

__attribute__((visibility("default"))) uint8_t* msl_trace_map(size_t* size)
{
    *size = MSL_TRACE_BYTES;
    return trace_map;
}

__attribute__((visibility("default"))) void msl_trace_reset(void)
{
    memset(trace_map, 0, sizeof(trace_map));
}

// Worker processes end without running exit handlers, so the line counts of a
// --coverage build are written out on request, after each tape.
extern void __gcov_dump(void);
extern void __gcov_reset(void);

__attribute__((visibility("default"))) void msl_trace_flush(void)
{
    __gcov_dump();
    __gcov_reset();
}
