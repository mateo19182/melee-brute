#ifndef MSL_API_H
#define MSL_API_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(MSL_CORE_SHARED) && defined(__GNUC__)
#define MSL_API __attribute__((visibility("default")))
#else
#define MSL_API
#endif

enum {
  MSL_MAX_PLAYERS = 4,
  MSL_MAX_ITEMS = 15,
  MSL_CHECKPOINT_PAGE_SIZE = 4096,
};

typedef struct MslBatch MslBatch;

typedef enum MslResult {
  MSL_OK = 0,
  MSL_INVALID_ARGUMENT = 1,
  MSL_OUT_OF_MEMORY = 2,
  MSL_INVALID_STATE = 3,
  MSL_INCOMPATIBLE = 4,
} MslResult;

typedef enum MslCharacter {
  MSL_CHARACTER_MARIO = 0,
  MSL_CHARACTER_FOX = 1,
  MSL_CHARACTER_MEWTWO = 16,
  MSL_CHARACTER_GAMEWATCH = 24,
  MSL_CHARACTER_CAPTAIN_FALCON = 2,
  MSL_CHARACTER_DONKEY_KONG = 3,
  MSL_CHARACTER_GANONDORF = 25,
  MSL_CHARACTER_YOSHI = 14,
  MSL_CHARACTER_BOWSER = 5,
  MSL_CHARACTER_SHEIK = 7,
  MSL_CHARACTER_PEACH = 9,
  MSL_CHARACTER_ICE_CLIMBERS = 10,
  MSL_CHARACTER_PIKACHU = 12,
  MSL_CHARACTER_PICHU = 23,
  MSL_CHARACTER_KIRBY = 4,
  MSL_CHARACTER_SAMUS = 13,
  MSL_CHARACTER_NESS = 8,
  MSL_CHARACTER_LINK = 6,
  MSL_CHARACTER_YOUNG_LINK = 20,
  MSL_CHARACTER_JIGGLYPUFF = 15,
  MSL_CHARACTER_LUIGI = 17,
  MSL_CHARACTER_DRMARIO = 21,
  MSL_CHARACTER_MARTH = 18,
  MSL_CHARACTER_ROY = 26,
  MSL_CHARACTER_ZELDA = 19,
  MSL_CHARACTER_FALCO = 22,
} MslCharacter;

typedef enum MslStage {
  MSL_STAGE_FOUNTAIN_OF_DREAMS = 2,
  MSL_STAGE_POKEMON_STADIUM = 3,
  MSL_STAGE_YOSHIS_STORY = 8,
  MSL_STAGE_DREAM_LAND_N64 = 28,
  MSL_STAGE_BATTLEFIELD = 31,
  MSL_STAGE_FINAL_DESTINATION = 32,
} MslStage;

typedef enum MslWhispyBlowDirection {
  MSL_WHISPY_NONE = 0,
  MSL_WHISPY_LEFT = 1,
  MSL_WHISPY_RIGHT = 2,
} MslWhispyBlowDirection;

typedef enum MslButton {
  MSL_BUTTON_D_DOWN = 0x0004,
  MSL_BUTTON_D_UP = 0x0008,
  MSL_BUTTON_Z = 0x0010,
  MSL_BUTTON_R = 0x0020,
  MSL_BUTTON_L = 0x0040,
  MSL_BUTTON_A = 0x0100,
  MSL_BUTTON_B = 0x0200,
  MSL_BUTTON_X = 0x0400,
  MSL_BUTTON_Y = 0x0800,
  MSL_BUTTON_START = 0x1000,
} MslButton;

typedef enum MslFacing {
  MSL_FACING_LEFT = -1,
  MSL_FACING_AUTO = 0,
  MSL_FACING_RIGHT = 1,
} MslFacing;

typedef enum MslTeamRelation {
  MSL_TEAM_SELF = 0,
  MSL_TEAM_ALLY = 1,
  MSL_TEAM_OPPONENT = 2,
} MslTeamRelation;

enum {
  MSL_TEAM_AUTO = -1,
  MSL_CONTROLLER_PORT_AUTO = -1,
};

typedef struct MslPlayerConfig {
  uint8_t character;
  int8_t team;
  int8_t facing;
  int8_t controller_port;
  uint8_t costume;
  uint8_t handicap;
  uint8_t start_percent; /* Initial damage, 0..100; zero on later stock respawns. */
} MslPlayerConfig;

typedef struct MslMatchConfig {
  uint32_t stage;
  uint32_t random_seed;
  int32_t max_frame;
  float damage_ratio;
  uint8_t num_players;
  uint8_t is_teams;
  uint8_t friendly_fire;
  uint8_t stocks;
  uint8_t viewpoint_player;
  uint8_t ucf_cardinals;
  MslPlayerConfig players[MSL_MAX_PLAYERS];
} MslMatchConfig;

typedef struct MslInputPlayer {
  uint16_t buttons;
  int8_t main_x;
  int8_t main_y;
  int8_t c_x;
  int8_t c_y;
  uint8_t l;
  uint8_t r;
} MslInputPlayer;

typedef struct MslInput {
  MslInputPlayer players[MSL_MAX_PLAYERS];
} MslInput;

typedef struct MslItem {
  uint8_t exists;
  uint8_t state;
  uint16_t type;
  int8_t owner;
  uint8_t _pad0;
  uint16_t instance_id;
  uint16_t attack_id;
  uint16_t attack_instance;
  float direction;
  float vel_x;
  float vel_y;
  float pos_x;
  float pos_y;
  uint16_t damage;
  uint16_t _pad1;
  float timer;
  uint32_t spawn_id;
  uint8_t misc0;
  uint8_t misc1;
  uint8_t misc2;
  uint8_t misc3;
} MslItem;

typedef struct MslObservationPlayer {
  uint8_t present; /* Fighter is awake; roster metadata remains when absent. */
  uint8_t source_player; /* UINT8_MAX for unused roster slots. */
  uint8_t team_relation;
  uint8_t team_id;
  float pos_x;
  float pos_y;
  float speed_air_x_self;
  float speed_ground_x_self;
  float speed_y_self;
  float speed_x_attack;
  float speed_y_attack;
  float percent;
  float shield_hp;
  uint16_t action_id;
  int16_t action_frame;
  uint16_t hitlag;
  uint16_t hitstun;
  uint8_t char_id;
  uint8_t stocks;
  uint8_t facing;
  uint8_t on_ground;
  uint8_t jumps_left;
  uint8_t hurtbox_state;
  uint8_t invulnerable;
  uint8_t _pad0;
} MslObservationPlayer;

typedef struct MslObservationRandall {
  uint8_t exists;
  uint8_t _pad0[3];
  float x;
  float y;
} MslObservationRandall;

typedef struct MslObservationFodPlatforms {
  float left;
  float right;
} MslObservationFodPlatforms;

typedef struct MslObservationStage {
  MslObservationRandall randall;
  MslObservationFodPlatforms fod_platforms;
  uint8_t whispy; // MSL_WHISPY_*; NONE outside Dream Land.
  uint8_t _pad0[3];
} MslObservationStage;

typedef struct MslObservation {
  int32_t frame_id;
  uint32_t frame_pre_random_seed;
  uint32_t stage_id;
  uint8_t num_players;
  uint8_t viewpoint_player;
  uint8_t is_teams;
  uint8_t _pad0;
  MslObservationStage stage;
  MslObservationPlayer slots[MSL_MAX_PLAYERS];
  MslItem items[MSL_MAX_ITEMS];
  // Ice Climbers' Nana: followers[k] is the follower of the player in
  // slots[k], present only while Slippi would record a follower row for her
  // (awake, including her death animation; not while asleep before Rebirth).
  MslObservationPlayer followers[MSL_MAX_PLAYERS];
} MslObservation;

typedef struct MslTerminal {
  int32_t frame_id;
  uint32_t stage_id;
  uint8_t done;
  uint8_t match_ended;
  uint8_t stockout;
  uint8_t max_frame_reached;
  uint8_t alive_count;
  uint8_t alive_team_count;
  uint8_t team_alive_mask;
  uint8_t _pad0;
} MslTerminal;

MSL_API const char* msl_result_string(MslResult result);
MSL_API MslMatchConfig msl_match_config_default(void);

// Immutable game data is loaded once per process, on the first
// msl_batch_create or msl_game_data_acquire, and shared by every batch; a
// process holds a single data root. Acquire it ahead of time to pay the load
// once for processes forked afterwards, which then share its pages
// copy-on-write. Each acquire is balanced by a release; the data is freed
// when no batch or acquire holds it.
MSL_API MslResult msl_game_data_acquire(const char* data_root);
MSL_API void msl_game_data_release(void);
MSL_API uint32_t msl_game_data_references(void);

// Different batches may be used concurrently, including creation/destruction.
// Synchronize access to each batch (both batches for copy) and caller buffers.
// data_root is the extraction root or its raw/ directory.
MSL_API MslResult msl_batch_create(const char* data_root, uint32_t batch_size,
                                   MslBatch** out_batch);
MSL_API void msl_batch_destroy(MslBatch* batch);
MSL_API uint32_t msl_batch_size(const MslBatch* batch);

// A NULL reset mask resets every environment; otherwise nonzero bytes select.
MSL_API MslResult msl_batch_reset(MslBatch* batch, const MslMatchConfig configs[],
                                  const uint8_t reset_mask[], MslObservation observations[]);
MSL_API MslResult msl_batch_step(MslBatch* batch, const MslInput inputs[],
                                 MslObservation observations[], MslTerminal terminals[]);
// A NULL step mask steps every environment; otherwise nonzero bytes select.
// Every environment is observed regardless of the mask, so unstepped matches
// republish their current state (e.g. a fresh post-reset entry frame).
MSL_API MslResult msl_batch_step_masked(MslBatch* batch, const MslInput inputs[],
                                        const uint8_t step_mask[],
                                        MslObservation observations[], MslTerminal terminals[]);
MSL_API MslResult msl_batch_observe(const MslBatch* batch, MslObservation observations[],
                                    MslTerminal terminals[]);

MSL_API MslResult msl_batch_copy(MslBatch* destination, const MslBatch* source,
                                 const uint32_t destination_indices[],
                                 const uint32_t source_indices[], uint32_t count);
// In-place checkpoint and rollback of one environment, for search. `pages` lists the
// MSL_CHECKPOINT_PAGE_SIZE-byte pages of environment memory to copy; NULL copies all of them.
// msl_batch_diff_pages sets page_flags[i] for each page that differs from the checkpoint. A
// checkpoint is only valid for the environment it was taken from, until that environment is reset,
// restored or copied into.
MSL_API MslResult msl_batch_checkpoint_size(const MslBatch* batch, uint32_t env_index,
                                            size_t* required_size);
MSL_API MslResult msl_batch_checkpoint(const MslBatch* batch, uint32_t env_index, void* buffer,
                                       size_t buffer_size, const uint32_t pages[],
                                       uint32_t page_count);
MSL_API MslResult msl_batch_rollback(MslBatch* batch, uint32_t env_index, const void* buffer,
                                     size_t buffer_size, const uint32_t pages[],
                                     uint32_t page_count);
MSL_API MslResult msl_batch_diff_pages(const MslBatch* batch, uint32_t env_index,
                                       const void* buffer, size_t buffer_size,
                                       uint8_t page_flags[], uint32_t flag_count);
MSL_API MslResult msl_batch_save_size(const MslBatch* batch, uint32_t env_index,
                                      size_t* required_size);
MSL_API MslResult msl_batch_save(const MslBatch* batch, uint32_t env_index, void* buffer,
                                 size_t buffer_size, size_t* written);
MSL_API MslResult msl_batch_restore(MslBatch* batch, uint32_t env_index, const void* buffer,
                                    size_t buffer_size);

// --- Search over one environment (Linux only; elsewhere every call returns MSL_INVALID_STATE)
//
// Take-back: msl_brute_checkpoint remembers the environment's state as `level` and
// msl_brute_rollback returns to it, exactly. The environment's memory is write-protected, so the
// first write to a page is caught and only pages that were written are ever copied. Destroy the
// MslBrute before its batch. After anything other than stepping changes the environment (reset,
// restore, copy), call msl_brute_reset: it forgets every checkpoint.
//
// msl_brute_hash is a 128-bit hash of the complete state: equal hashes mean equal states, within
// one process and its forks.
typedef struct MslBrute MslBrute;

enum {
  MSL_BRUTE_MAX_MOVES = 64,
  MSL_BRUTE_MAX_MOVE_FRAMES = 512,
  MSL_BRUTE_MAX_MENUS = 16,
  MSL_BRUTE_MAX_MENU_MOVES = 32,
  MSL_BRUTE_ACTIONS = 1024,
  MSL_BRUTE_NO_MOVE = -1,
};

// A move is a run of inputs, one per frame, stick x written as if the opponent were to the right:
// frames[move_start[m] .. move_start[m + 1]). A fighter whose action id is `a` may pick the moves
// of menu action_menu[a] (ids from MSL_BRUTE_ACTIONS up use the last entry). With `trap` set, a
// position where the defender's action is in trap_defender, the attacker's is in trap_attacker and
// free to pick, they are within trap_reach of each other and (trap_same_y) at the same height
// counts as won by the attacker.
typedef struct MslBruteRules {
  uint32_t move_count;
  uint32_t menu_count;
  uint16_t move_start[MSL_BRUTE_MAX_MOVES + 1];
  MslInputPlayer frames[MSL_BRUTE_MAX_MOVE_FRAMES];
  uint8_t menu_size[MSL_BRUTE_MAX_MENUS];
  uint8_t menu_moves[MSL_BRUTE_MAX_MENUS][MSL_BRUTE_MAX_MENU_MOVES];
  uint8_t action_menu[MSL_BRUTE_ACTIONS];
  uint8_t trap;
  uint8_t trap_same_y;
  uint8_t _reserved[2];
  float trap_reach;
  uint8_t trap_attacker[MSL_BRUTE_ACTIONS];
  uint8_t trap_defender[MSL_BRUTE_ACTIONS];
} MslBruteRules;

// A move in flight is move | frame << 8 | (1 << 24 if it is played facing left), or
// MSL_BRUTE_NO_MOVE when the fighter is free to pick.
typedef struct MslBrutePlay {
  int32_t outcome; // +1 player 1 landed the first hit, -1 was hit or traded, 0 nobody hit yet
  int32_t frames;  // frames played
  int32_t attacker; // the unfinished moves afterwards
  int32_t defender;
} MslBrutePlay;

typedef struct MslBruteStats {
  uint64_t positions; // joint moves played
  uint64_t frames;    // frames simulated
  uint64_t nodes;     // positions expanded by the search
  uint64_t table_hits;
  uint64_t table_stores;
  uint32_t dirty_pages; // pages a rollback copies
  uint32_t _reserved;
} MslBruteStats;

MSL_API MslResult msl_brute_create(MslBatch* batch, uint32_t env_index, uint32_t level_count,
                                   MslBrute** out_brute);
MSL_API void msl_brute_destroy(MslBrute* brute);
MSL_API MslResult msl_brute_reset(MslBrute* brute);
MSL_API MslResult msl_brute_checkpoint(MslBrute* brute, uint32_t level);
MSL_API MslResult msl_brute_rollback(MslBrute* brute, uint32_t level);
MSL_API MslResult msl_brute_hash(MslBrute* brute, uint64_t hash[2]);
// One frame, without writing an observation.
MSL_API MslResult msl_brute_step(MslBrute* brute, const MslInput* input);

// The forced-first-hit search. msl_brute_begin takes the current percents and stocks as the ones
// a hit is measured against. `table` is memory for decided positions, shared by every process
// that passes the same mapping; NULL searches without one. With `exact` a decided position answers
// only for the very same state (msl_brute_hash). Without, it answers for every state that looks the
// same from outside (place and speeds to 0.01, action and its frame, hitlag, shield, facing, jumps
// left): far fewer positions to search, but the inputs a fighter was holding are not part of that
// look, so an answer can be wrong. msl_brute_search answers, for
// `attacking`, whether player 1 forces the first hit within `frames` whatever player 2 does, and
// otherwise whether player 2 can avoid it whatever player 1 does; the side asked about picks
// first. It uses checkpoint levels from `level` up and leaves the state as it found it.
MSL_API MslResult msl_brute_begin(MslBrute* brute, const MslBruteRules* rules, void* table,
                                  size_t table_bytes, int exact);
MSL_API MslResult msl_brute_play(MslBrute* brute, int32_t attacker, int32_t defender,
                                 MslBrutePlay* out);
MSL_API MslResult msl_brute_options(MslBrute* brute, int32_t attacker, int32_t defender,
                                    int32_t attackers[], uint32_t* attacker_count,
                                    int32_t defenders[], uint32_t* defender_count);
// Whether the rules' trap holds in the current state, for an attacker free to pick.
MSL_API MslResult msl_brute_trapped(MslBrute* brute, int32_t* out_trapped);
MSL_API MslResult msl_brute_search(MslBrute* brute, int32_t frames, int32_t attacker,
                                   int32_t defender, uint32_t level, int attacking,
                                   int32_t* out_holds);
MSL_API MslResult msl_brute_stats(const MslBrute* brute, MslBruteStats* out);

#ifdef __cplusplus
}
#endif

#endif
