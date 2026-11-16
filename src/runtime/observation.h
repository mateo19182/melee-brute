#ifndef MSL_CORE_RUNTIME_OBSERVATION_H
#define MSL_CORE_RUNTIME_OBSERVATION_H

#include "runtime/wire.h"

typedef struct MslCoreMatch MslCoreMatch;

int msl_core_match_write_observation(const MslCoreMatch* match,
                                     uint8_t viewpoint_player,
                                     MslCoreObservation* output);
int msl_core_match_write_observation_from_compare(
    const MslCoreMatch* match, uint8_t viewpoint_player,
    MslCoreObservation* output);
int msl_match_randall_position(const MslCoreMatch* match, float* x, float* y);
void msl_core_canonicalize_production_items(
    MslCoreItem items[MSL_CORE_MAX_ITEMS]);
void msl_core_match_write_terminal(const MslCoreMatch* match,
                                   int32_t max_frame_id,
                                   MslCoreTerminal* output);

// The fields the forced-first-hit search reads, as the observation reports
// them: a sleeping fighter keeps its stocks and zeroes the rest.
typedef struct MslCoreBruteView {
    float pos_x;
    float pos_y;
    float percent;
    uint16_t action_id;
    uint8_t stocks;
} MslCoreBruteView;
void msl_core_match_brute_view(const MslCoreMatch* match, int player,
                               MslCoreBruteView* out);
// What a player looks like from outside, coarsely: position and speeds to
// 0.01, action and its frame, hitlag, shield to 0.1, facing, jumps left.
enum { MSL_CORE_BRUTE_LOOK_FIELDS = 11 };
void msl_core_match_brute_look(const MslCoreMatch* match, int player,
                               int64_t out[MSL_CORE_BRUTE_LOOK_FIELDS]);

#endif
