#ifndef MSL_CORE_RUNTIME_BRUTE_H
#define MSL_CORE_RUNTIME_BRUTE_H

#include "runtime/batch.h"

// Search over one match: exact take-back, a hash of the whole match state,
// and the two-player forced-first-hit search. See api.h for the contract.
typedef struct MslCoreBrute MslCoreBrute;

MslCoreResult msl_core_brute_create(MslCoreBatch* batch, uint32_t match_index,
                                    uint32_t level_count,
                                    MslCoreBrute** out_brute);
void msl_core_brute_destroy(MslCoreBrute* brute);
MslCoreResult msl_core_brute_reset(MslCoreBrute* brute);
MslCoreResult msl_core_brute_checkpoint(MslCoreBrute* brute, uint32_t level);
MslCoreResult msl_core_brute_rollback(MslCoreBrute* brute, uint32_t level);
MslCoreResult msl_core_brute_hash(MslCoreBrute* brute, uint64_t hash[2]);
MslCoreResult msl_core_brute_step(MslCoreBrute* brute, const MslInput* input);

MslCoreResult msl_core_brute_begin(MslCoreBrute* brute,
                                   const MslBruteRules* rules, void* table,
                                   size_t table_bytes, int exact);
MslCoreResult msl_core_brute_play(MslCoreBrute* brute, int32_t attacker,
                                  int32_t defender, MslBrutePlay* out);
MslCoreResult msl_core_brute_options(MslCoreBrute* brute, int32_t attacker,
                                     int32_t defender, int32_t attackers[],
                                     uint32_t* attacker_count,
                                     int32_t defenders[],
                                     uint32_t* defender_count);
MslCoreResult msl_core_brute_trapped(MslCoreBrute* brute, int32_t* out_trapped);
MslCoreResult msl_core_brute_ruled(MslCoreBrute* brute, int32_t frames,
                                   int32_t attacker, int32_t defender,
                                   int32_t* out_whose);
MslCoreResult msl_core_brute_search(MslCoreBrute* brute, int32_t frames,
                                    int32_t attacker, int32_t defender,
                                    uint32_t level, int attacking,
                                    uint32_t spread, int32_t* out_holds);
MslCoreResult msl_core_brute_stats(const MslCoreBrute* brute,
                                   MslBruteStats* out);

#endif
