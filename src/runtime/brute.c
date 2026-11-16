#include "runtime/brute.h"

#include "runtime/observation.h"
#include "runtime/scalar.h"

#include <stdlib.h>
#include <string.h>

#if defined(MSL_CORE_NATIVE) && !defined(MSL_CORE_WASM) && defined(__linux__)

#include <pthread.h>
#include <signal.h>
#include <sys/mman.h>
#if defined(__AES__)
#include <immintrin.h>
#endif

// Take-back without copying the match: its memory is write-protected, the
// first write to a page faults, and the handler keeps the page's content from
// before that write and lets the write through. A page that never faulted
// still holds what it held when protection began, so a checkpoint is a copy
// of the written pages alone and a rollback is exact by construction.
//
// Pages are numbered in the order they were first written. A checkpoint taken
// when `n` pages had been written holds pages 0..n; a page written later had,
// at the time of that checkpoint, the content the handler kept.

enum {
    BRUTE_PAGE = 4096,
    BRUTE_REGISTRY = 256,
    BRUTE_LEVEL_UNSET = UINT32_MAX,
    BRUTE_TABLE_WAYS = 4,
    BRUTE_NO_WIN = 0xFFF,
};

typedef struct BruteSpan {
    uint8_t* base;
    uint32_t pages;
    uint32_t first_unit;
} BruteSpan;

// The bytes of a region outside its whole pages share those pages with other
// owners, so they stay unprotected and every checkpoint copies them.
typedef struct BruteFragment {
    uint8_t* base;
    uint32_t size;
} BruteFragment;

typedef struct BruteEntry {
    uint64_t lock; // key ^ data: a torn or foreign entry fails the check
    uint64_t data; // win:12 | safe:12 | cost:6 | check:32
} BruteEntry;

typedef struct BruteRecord {
    uint32_t tries;
    uint32_t wins;
    uint64_t positions; // spent on the tries
} BruteRecord;

struct MslCoreBrute {
    MslCoreMatch* match;
    BruteSpan spans[2];
    uint32_t span_count;
    BruteFragment fragments[4];
    uint32_t fragment_count;
    uint32_t fragment_bytes;
    uint32_t unit_count;
    uint8_t* written; // by unit
    uint8_t** dirty;  // page addresses, in the order they were first written
    volatile uint32_t dirty_count;
    uint8_t* original; // dirty[i]'s content before its first write
    uint64_t (*original_hash)[2];
    uint8_t* levels;
    size_t level_stride;
    uint32_t level_count;
    uint32_t* level_dirty;
    int registry_slot;

    MslBruteRules rules;
    int has_rules;
    double base_percent[2];
    int base_stocks[2];
    uint64_t salt;
    BruteEntry* table;
    uint64_t table_mask; // of buckets
    int failed;
    MslBruteStats stats;
    // Move ordering, by question asked: how often a move held, how often an
    // answer refuted any move, and how often it refuted this particular one.
    BruteRecord held[2][MSL_BRUTE_MAX_MOVES];
    BruteRecord refuted[2][MSL_BRUTE_MAX_MOVES];
    BruteRecord countered[2][MSL_BRUTE_MAX_MOVES][MSL_BRUTE_MAX_MOVES];
};

static MslCoreBrute* volatile registry[BRUTE_REGISTRY];
static pthread_mutex_t registry_mutex = PTHREAD_MUTEX_INITIALIZER;
static struct sigaction previous_action;
static int previous_action_saved;

// --- hashing

static inline uint64_t mix64(uint64_t a, uint64_t b)
{
    __uint128_t product = (__uint128_t) a * b;
    return (uint64_t) product ^ (uint64_t) (product >> 64);
}

// `size` is a multiple of 64.
static void hash_blocks(const uint8_t* bytes, size_t size, uint64_t tweak,
                       uint64_t out[2])
{
#if defined(__AES__)
    __m128i s0 = _mm_set_epi64x((long long) tweak, 0x243F6A8885A308D3LL);
    __m128i s1 = _mm_set_epi64x(0x13198A2E03707344LL, (long long) tweak);
    __m128i s2 = _mm_set_epi64x((long long) ~tweak, 0xA4093822299F31D0LL);
    __m128i s3 = _mm_set_epi64x(0x082EFA98EC4E6C89LL, (long long) ~tweak);
    const __m128i k = _mm_set_epi64x(0x452821E638D01377LL, 0xBE5466CF34E90C6CLL);
    size_t i;
    for (i = 0; i < size; i += 64) {
        s0 = _mm_aesenc_si128(s0, _mm_loadu_si128((const void*) (bytes + i)));
        s1 = _mm_aesenc_si128(s1, _mm_loadu_si128((const void*) (bytes + i + 16)));
        s2 = _mm_aesenc_si128(s2, _mm_loadu_si128((const void*) (bytes + i + 32)));
        s3 = _mm_aesenc_si128(s3, _mm_loadu_si128((const void*) (bytes + i + 48)));
    }
    s0 = _mm_aesenc_si128(s0, s1);
    s2 = _mm_aesenc_si128(s2, s3);
    s0 = _mm_aesenc_si128(s0, s2);
    s0 = _mm_aesenc_si128(s0, k);
    s0 = _mm_aesenc_si128(s0, k);
    s0 = _mm_aesenc_si128(s0, k);
    _mm_storeu_si128((void*) out, s0);
#else
    uint64_t a = tweak ^ 0x243F6A8885A308D3ULL;
    uint64_t b = ~tweak ^ 0x13198A2E03707344ULL;
    size_t i;
    for (i = 0; i < size; i += 16) {
        uint64_t x;
        uint64_t y;
        memcpy(&x, bytes + i, 8);
        memcpy(&y, bytes + i + 8, 8);
        a = mix64(a ^ x, 0xA4093822299F31D0ULL ^ y);
        b = mix64(b ^ y, 0x082EFA98EC4E6C89ULL ^ x) + a;
    }
    out[0] = mix64(a, 0x452821E638D01377ULL ^ b);
    out[1] = mix64(b, 0xBE5466CF34E90C6CULL ^ a);
#endif
}

static void hash_bytes(const uint8_t* bytes, size_t size, uint64_t tweak,
                       uint64_t out[2])
{
    size_t whole = size & ~(size_t) 63;
    uint8_t tail[64] = { 0 };
    uint64_t part[2];
    hash_blocks(bytes, whole, tweak, out);
    if (whole != size) {
        memcpy(tail, bytes + whole, size - whole);
        hash_blocks(tail, sizeof(tail), ~tweak + size, part);
        out[0] ^= part[0];
        out[1] ^= part[1];
    }
}

// --- write protection

static void brute_fault(int signal_number, siginfo_t* info, void* context)
{
    uint8_t* address = (uint8_t*) info->si_addr;
    int slot;
    for (slot = 0; slot < BRUTE_REGISTRY; ++slot) {
        MslCoreBrute* brute = registry[slot];
        uint32_t s;
        if (brute == NULL) {
            continue;
        }
        for (s = 0; s < brute->span_count; ++s) {
            const BruteSpan* span = &brute->spans[s];
            size_t offset = (size_t) (address - span->base);
            if (address >= span->base &&
                offset < (size_t) span->pages * BRUTE_PAGE)
            {
                uint32_t page = (uint32_t) (offset / BRUTE_PAGE);
                uint32_t unit = span->first_unit + page;
                uint8_t* start = span->base + (size_t) page * BRUTE_PAGE;
                uint32_t index = brute->dirty_count;
                if (brute->written[unit]) {
                    return;
                }
                brute->written[unit] = 1;
                brute->dirty[index] = start;
                memcpy(brute->original + (size_t) index * BRUTE_PAGE, start,
                       BRUTE_PAGE);
                hash_blocks(start, BRUTE_PAGE, (uint64_t) (uintptr_t) start,
                            brute->original_hash[index]);
                brute->dirty_count = index + 1;
                mprotect(start, BRUTE_PAGE, PROT_READ | PROT_WRITE);
                return;
            }
        }
    }
    // Not a protected page: hand the fault to whoever had the signal before.
    if (previous_action.sa_flags & SA_SIGINFO) {
        previous_action.sa_sigaction(signal_number, info, context);
    } else if (previous_action.sa_handler != SIG_DFL &&
               previous_action.sa_handler != SIG_IGN)
    {
        previous_action.sa_handler(signal_number);
    } else {
        signal(signal_number, SIG_DFL);
    }
}

static void install_handler(void)
{
    struct sigaction action;
    struct sigaction current;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = brute_fault;
    action.sa_flags = SA_SIGINFO | SA_NODEFER;
    sigemptyset(&action.sa_mask);
    pthread_mutex_lock(&registry_mutex);
    sigaction(SIGSEGV, NULL, &current);
    if (!(current.sa_flags & SA_SIGINFO) ||
        current.sa_sigaction != brute_fault)
    {
        previous_action = current;
        previous_action_saved = 1;
        sigaction(SIGSEGV, &action, NULL);
    }
    pthread_mutex_unlock(&registry_mutex);
}

static void protect(MslCoreBrute* brute, int protection)
{
    uint32_t s;
    for (s = 0; s < brute->span_count; ++s) {
        mprotect(brute->spans[s].base,
                 (size_t) brute->spans[s].pages * BRUTE_PAGE, protection);
    }
}

static void add_region(MslCoreBrute* brute, uint8_t* start, size_t size)
{
    uint8_t* end = start + size;
    uint8_t* first =
        (uint8_t*) (((uintptr_t) start + BRUTE_PAGE - 1) & ~(uintptr_t) (BRUTE_PAGE - 1));
    uint8_t* last = (uint8_t*) ((uintptr_t) end & ~(uintptr_t) (BRUTE_PAGE - 1));
    if (first >= last) {
        first = last = end;
    } else {
        BruteSpan* span = &brute->spans[brute->span_count++];
        span->base = first;
        span->pages = (uint32_t) ((size_t) (last - first) / BRUTE_PAGE);
        span->first_unit = brute->unit_count;
        brute->unit_count += span->pages;
    }
    if (first > start) {
        BruteFragment* fragment = &brute->fragments[brute->fragment_count++];
        fragment->base = start;
        fragment->size = (uint32_t) (first - start);
        brute->fragment_bytes += fragment->size;
    }
    if (end > last) {
        BruteFragment* fragment = &brute->fragments[brute->fragment_count++];
        fragment->base = last;
        fragment->size = (uint32_t) (end - last);
        brute->fragment_bytes += fragment->size;
    }
}

static void* map_lazy(size_t bytes)
{
    void* mapping = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    return mapping == MAP_FAILED ? NULL : mapping;
}

MslCoreResult msl_core_brute_create(MslCoreBatch* batch, uint32_t match_index,
                                    uint32_t level_count,
                                    MslCoreBrute** out_brute)
{
    MslCoreBrute* brute;
    MslCoreMatch* match;
    uint32_t i;
    int slot;
    if (out_brute == NULL || level_count == 0) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    *out_brute = NULL;
    match = msl_core_batch_match(batch, match_index);
    if (match == NULL) {
        return MSL_CORE_INVALID_STATE;
    }
    brute = calloc(1, sizeof(*brute));
    if (brute == NULL) {
        return MSL_CORE_OUT_OF_MEMORY;
    }
    brute->match = match;
    brute->registry_slot = -1;
    add_region(brute, (uint8_t*) match, sizeof(*match));
    add_region(brute, match->memory.arena, match->memory.capacity);
    brute->fragment_bytes = (brute->fragment_bytes + 63u) & ~63u;
    brute->level_count = level_count;
    brute->level_stride =
        ((size_t) brute->fragment_bytes + (size_t) brute->unit_count * BRUTE_PAGE +
         BRUTE_PAGE - 1) & ~(size_t) (BRUTE_PAGE - 1);
    brute->written = calloc(brute->unit_count, 1);
    brute->dirty = calloc(brute->unit_count, sizeof(*brute->dirty));
    brute->original_hash =
        calloc(brute->unit_count, sizeof(*brute->original_hash));
    brute->level_dirty = malloc(level_count * sizeof(*brute->level_dirty));
    brute->original = map_lazy((size_t) brute->unit_count * BRUTE_PAGE);
    brute->levels = map_lazy(brute->level_stride * level_count);
    if (brute->written == NULL || brute->dirty == NULL ||
        brute->original_hash == NULL || brute->level_dirty == NULL ||
        brute->original == NULL || brute->levels == NULL)
    {
        msl_core_brute_destroy(brute);
        return MSL_CORE_OUT_OF_MEMORY;
    }
    for (i = 0; i < level_count; ++i) {
        brute->level_dirty[i] = BRUTE_LEVEL_UNSET;
    }
    install_handler();
    pthread_mutex_lock(&registry_mutex);
    for (slot = 0; slot < BRUTE_REGISTRY; ++slot) {
        if (registry[slot] == NULL) {
            registry[slot] = brute;
            brute->registry_slot = slot;
            break;
        }
    }
    pthread_mutex_unlock(&registry_mutex);
    if (brute->registry_slot < 0) {
        msl_core_brute_destroy(brute);
        return MSL_CORE_OUT_OF_MEMORY;
    }
    protect(brute, PROT_READ);
    *out_brute = brute;
    return MSL_CORE_OK;
}

void msl_core_brute_destroy(MslCoreBrute* brute)
{
    if (brute == NULL) {
        return;
    }
    if (brute->registry_slot >= 0) {
        protect(brute, PROT_READ | PROT_WRITE);
        pthread_mutex_lock(&registry_mutex);
        registry[brute->registry_slot] = NULL;
        pthread_mutex_unlock(&registry_mutex);
    }
    if (brute->original != NULL) {
        munmap(brute->original, (size_t) brute->unit_count * BRUTE_PAGE);
    }
    if (brute->levels != NULL) {
        munmap(brute->levels, brute->level_stride * brute->level_count);
    }
    free(brute->written);
    free(brute->dirty);
    free(brute->original_hash);
    free(brute->level_dirty);
    free(brute);
}

MslCoreResult msl_core_brute_reset(MslCoreBrute* brute)
{
    uint32_t i;
    if (brute == NULL) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    install_handler();
    protect(brute, PROT_READ);
    memset(brute->written, 0, brute->unit_count);
    brute->dirty_count = 0;
    for (i = 0; i < brute->level_count; ++i) {
        brute->level_dirty[i] = BRUTE_LEVEL_UNSET;
    }
    return MSL_CORE_OK;
}

static inline uint8_t* level_buffer(const MslCoreBrute* brute, uint32_t level)
{
    return brute->levels + brute->level_stride * level;
}

static inline void checkpoint(MslCoreBrute* brute, uint32_t level)
{
    uint8_t* buffer = level_buffer(brute, level);
    uint32_t count = brute->dirty_count;
    uint32_t i;
    for (i = 0; i < brute->fragment_count; ++i) {
        memcpy(buffer, brute->fragments[i].base, brute->fragments[i].size);
        buffer += brute->fragments[i].size;
    }
    buffer = level_buffer(brute, level) + brute->fragment_bytes;
    for (i = 0; i < count; ++i) {
        memcpy(buffer + (size_t) i * BRUTE_PAGE, brute->dirty[i], BRUTE_PAGE);
    }
    brute->level_dirty[level] = count;
}

static inline void rollback(MslCoreBrute* brute, uint32_t level)
{
    const uint8_t* buffer = level_buffer(brute, level);
    uint32_t saved = brute->level_dirty[level];
    uint32_t count = brute->dirty_count;
    uint32_t i;
    for (i = 0; i < brute->fragment_count; ++i) {
        memcpy(brute->fragments[i].base, buffer, brute->fragments[i].size);
        buffer += brute->fragments[i].size;
    }
    buffer = level_buffer(brute, level) + brute->fragment_bytes;
    for (i = 0; i < saved; ++i) {
        memcpy(brute->dirty[i], buffer + (size_t) i * BRUTE_PAGE, BRUTE_PAGE);
    }
    for (; i < count; ++i) {
        memcpy(brute->dirty[i], brute->original + (size_t) i * BRUTE_PAGE,
               BRUTE_PAGE);
    }
}

MslCoreResult msl_core_brute_checkpoint(MslCoreBrute* brute, uint32_t level)
{
    if (brute == NULL || level >= brute->level_count) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    checkpoint(brute, level);
    return MSL_CORE_OK;
}

MslCoreResult msl_core_brute_rollback(MslCoreBrute* brute, uint32_t level)
{
    if (brute == NULL || level >= brute->level_count) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    if (brute->level_dirty[level] == BRUTE_LEVEL_UNSET) {
        return MSL_CORE_INVALID_STATE;
    }
    rollback(brute, level);
    return MSL_CORE_OK;
}

// A page that still holds its original content adds nothing, so the hash does
// not depend on which pages happen to have been written so far.
static void state_hash(MslCoreBrute* brute, uint64_t hash[2])
{
    uint64_t part[2];
    uint32_t count = brute->dirty_count;
    uint32_t i;
    hash[0] = 0;
    hash[1] = 0;
    for (i = 0; i < brute->fragment_count; ++i) {
        hash_bytes(brute->fragments[i].base, brute->fragments[i].size,
                   (uint64_t) (uintptr_t) brute->fragments[i].base, part);
        hash[0] ^= part[0];
        hash[1] ^= part[1];
    }
    for (i = 0; i < count; ++i) {
        hash_blocks(brute->dirty[i], BRUTE_PAGE,
                    (uint64_t) (uintptr_t) brute->dirty[i], part);
        hash[0] ^= part[0] ^ brute->original_hash[i][0];
        hash[1] ^= part[1] ^ brute->original_hash[i][1];
    }
}

MslCoreResult msl_core_brute_hash(MslCoreBrute* brute, uint64_t hash[2])
{
    if (brute == NULL || hash == NULL) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    state_hash(brute, hash);
    return MSL_CORE_OK;
}

// --- playing

static inline int step(MslCoreBrute* brute, const MslCoreInput* input)
{
    static const MslCoreStageEvents no_stage_events = { 0 };
    brute->stats.frames += 1;
    if (msl_core_match_step(brute->match, input, brute->match->random_seed,
                            &no_stage_events) != 0)
    {
        brute->failed = 1;
        return -1;
    }
    return 0;
}

static inline void set_pad(MslCoreInputPlayer* out, const MslInputPlayer* in,
                           int toward)
{
    out->buttons = in->buttons;
    out->main_x = (int8_t) (in->main_x * toward);
    out->main_y = in->main_y;
    out->c_x = (int8_t) (in->c_x * toward);
    out->c_y = in->c_y;
    out->l = in->l;
    out->r = in->r;
}

MslCoreResult msl_core_brute_step(MslCoreBrute* brute, const MslInput* input)
{
    MslCoreInput core;
    int player;
    if (brute == NULL || input == NULL) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    memset(&core, 0, sizeof(core));
    for (player = 0; player < MSL_CORE_MAX_PLAYERS; ++player) {
        set_pad(&core.p[player], &input->players[player], 1);
    }
    return step(brute, &core) == 0 ? MSL_CORE_OK : MSL_CORE_INVALID_STATE;
}

#define MOVE_ID(move) ((uint32_t) (move) & 0xFFu)
#define MOVE_FRAME(move) (((uint32_t) (move) >> 8) & 0xFFFFu)
#define MOVE_TOWARD(move) (((uint32_t) (move) >> 24) & 1u ? -1 : 1)
#define MOVE_PACK(id, frame, toward)                                          \
    ((int32_t) ((uint32_t) (id) | (uint32_t) (frame) << 8 |                   \
                ((toward) < 0 ? 1u << 24 : 0u)))

static inline uint32_t move_length(const MslBruteRules* rules, uint32_t id)
{
    return (uint32_t) rules->move_start[id + 1] - rules->move_start[id];
}

static inline int valid_move(const MslCoreBrute* brute, int32_t move)
{
    return move >= 0 && MOVE_ID(move) < brute->rules.move_count &&
           MOVE_FRAME(move) < move_length(&brute->rules, MOVE_ID(move)) &&
           ((uint32_t) move >> 25) == 0;
}

static inline void views(const MslCoreBrute* brute, MslCoreBruteView view[2])
{
    msl_core_match_brute_view(brute->match, 0, &view[0]);
    msl_core_match_brute_view(brute->match, 1, &view[1]);
}

static inline int first_hit(const MslCoreBrute* brute,
                            const MslCoreBruteView view[2])
{
    double hurt1 = (double) view[0].percent - brute->base_percent[0] +
                   100.0 * (brute->base_stocks[0] - (int) view[0].stocks);
    double hurt2 = (double) view[1].percent - brute->base_percent[1] +
                   100.0 * (brute->base_stocks[1] - (int) view[1].stocks);
    return hurt1 == 0.0 && hurt2 == 0.0 ? 0 : hurt2 > hurt1 ? 1 : -1;
}

// Both moves run until one of them ends or someone is hit.
static void play(MslCoreBrute* brute, int32_t attacker, int32_t defender,
                 MslBrutePlay* out, MslCoreBruteView view[2])
{
    const MslBruteRules* rules = &brute->rules;
    uint32_t a_id = MOVE_ID(attacker);
    uint32_t d_id = MOVE_ID(defender);
    uint32_t a_frame = MOVE_FRAME(attacker);
    uint32_t d_frame = MOVE_FRAME(defender);
    uint32_t a_left = move_length(rules, a_id) - a_frame;
    uint32_t d_left = move_length(rules, d_id) - d_frame;
    uint32_t frames = a_left < d_left ? a_left : d_left;
    const MslInputPlayer* a_rows = &rules->frames[rules->move_start[a_id] + a_frame];
    const MslInputPlayer* d_rows = &rules->frames[rules->move_start[d_id] + d_frame];
    MslCoreInput input;
    uint32_t used = 0;
    int outcome = 0;
    brute->stats.positions += 1;
    memset(&input, 0, sizeof(input));
    while (used < frames) {
        set_pad(&input.p[0], &a_rows[used], MOVE_TOWARD(attacker));
        set_pad(&input.p[1], &d_rows[used], MOVE_TOWARD(defender));
        used += 1;
        if (step(brute, &input) != 0) {
            break;
        }
        views(brute, view);
        outcome = first_hit(brute, view);
        if (outcome != 0) {
            break;
        }
    }
    out->outcome = outcome;
    out->frames = (int32_t) used;
    out->attacker = used < a_left
                        ? MOVE_PACK(a_id, a_frame + used, MOVE_TOWARD(attacker))
                        : MSL_BRUTE_NO_MOVE;
    out->defender = used < d_left
                        ? MOVE_PACK(d_id, d_frame + used, MOVE_TOWARD(defender))
                        : MSL_BRUTE_NO_MOVE;
}

static inline uint32_t menu_of(const MslBruteRules* rules, uint16_t action)
{
    return rules->action_menu[action < MSL_BRUTE_ACTIONS
                                  ? action
                                  : MSL_BRUTE_ACTIONS - 1];
}

static void options(const MslCoreBrute* brute, const MslCoreBruteView view[2],
                    int32_t attacker, int32_t defender, int32_t attackers[],
                    uint32_t* attacker_count, int32_t defenders[],
                    uint32_t* defender_count)
{
    const MslBruteRules* rules = &brute->rules;
    int toward = view[1].pos_x >= view[0].pos_x ? 1 : -1;
    uint32_t i;
    if (attacker >= 0) {
        attackers[0] = attacker;
        *attacker_count = 1;
    } else {
        uint32_t menu = menu_of(rules, view[0].action_id);
        for (i = 0; i < rules->menu_size[menu]; ++i) {
            attackers[i] = MOVE_PACK(rules->menu_moves[menu][i], 0, toward);
        }
        *attacker_count = rules->menu_size[menu];
    }
    if (defender >= 0) {
        defenders[0] = defender;
        *defender_count = 1;
    } else {
        uint32_t menu = menu_of(rules, view[1].action_id);
        for (i = 0; i < rules->menu_size[menu]; ++i) {
            defenders[i] = MOVE_PACK(rules->menu_moves[menu][i], 0, -toward);
        }
        *defender_count = rules->menu_size[menu];
    }
}

static inline int trapped(const MslCoreBrute* brute,
                          const MslCoreBruteView view[2])
{
    const MslBruteRules* rules = &brute->rules;
    float gap = view[1].pos_x - view[0].pos_x;
    return rules->trap && view[0].action_id < MSL_BRUTE_ACTIONS &&
           view[1].action_id < MSL_BRUTE_ACTIONS &&
           rules->trap_defender[view[1].action_id] &&
           rules->trap_attacker[view[0].action_id] &&
           (gap < 0 ? -gap : gap) <= rules->trap_reach &&
           (!rules->trap_same_y || view[0].pos_y == view[1].pos_y);
}

// --- decided positions
//
// For a key the table keeps the fewest frames in which the position is proven
// good for the attacker (more frames only help him) and the most frames for
// which it is proven good for the defender.

#define ENTRY_WIN(data) ((uint32_t) ((data) & 0xFFFu))
#define ENTRY_SAFE(data) ((uint32_t) (((data) >> 12) & 0xFFFu))
#define ENTRY_COST(data) ((uint32_t) (((data) >> 24) & 0x3Fu))
#define ENTRY_CHECK(data) ((uint32_t) ((data) >> 32))
#define ENTRY_PACK(win, safe, cost, check)                                    \
    ((uint64_t) (win) | (uint64_t) (safe) << 12 | (uint64_t) (cost) << 24 |   \
     (uint64_t) (check) << 32)

static inline BruteEntry* bucket_of(const MslCoreBrute* brute, uint64_t key)
{
    return brute->table + (key & brute->table_mask) * BRUTE_TABLE_WAYS;
}

static inline int table_find(const MslCoreBrute* brute, const uint64_t key[2],
                             uint32_t* win, uint32_t* safe)
{
    const BruteEntry* bucket = bucket_of(brute, key[0]);
    int way;
    for (way = 0; way < BRUTE_TABLE_WAYS; ++way) {
        uint64_t data = bucket[way].data;
        if ((bucket[way].lock ^ data) == key[0] &&
            ENTRY_CHECK(data) == (uint32_t) key[1] && data != 0)
        {
            *win = ENTRY_WIN(data);
            *safe = ENTRY_SAFE(data);
            return 1;
        }
    }
    return 0;
}

static void table_store(MslCoreBrute* brute, const uint64_t key[2], int frames,
                        int for_attacker, uint64_t positions)
{
    BruteEntry* bucket = bucket_of(brute, key[0]);
    uint32_t cost = 0;
    uint32_t win = BRUTE_NO_WIN;
    uint32_t safe = 0;
    int victim = 0;
    int way;
    while (positions > 1 && cost < 63) {
        positions >>= 1;
        cost += 1;
    }
    for (way = 0; way < BRUTE_TABLE_WAYS; ++way) {
        uint64_t data = bucket[way].data;
        if ((bucket[way].lock ^ data) == key[0] &&
            ENTRY_CHECK(data) == (uint32_t) key[1] && data != 0)
        {
            win = ENTRY_WIN(data);
            safe = ENTRY_SAFE(data);
            if (ENTRY_COST(data) > cost) {
                cost = ENTRY_COST(data);
            }
            victim = way;
            break;
        }
        if (data == 0) {
            if (bucket[victim].data != 0) {
                victim = way;
            }
        } else if (bucket[victim].data != 0 &&
                   ENTRY_COST(data) < ENTRY_COST(bucket[victim].data))
        {
            victim = way;
        }
    }
    if (for_attacker) {
        if ((uint32_t) frames < win) {
            win = (uint32_t) frames;
        }
    } else if ((uint32_t) frames > safe) {
        safe = (uint32_t) frames;
    }
    {
        uint64_t data = ENTRY_PACK(win, safe, cost, (uint32_t) key[1]);
        bucket[victim].data = data;
        bucket[victim].lock = key[0] ^ data;
    }
    brute->stats.table_stores += 1;
}

// What the search would answer in the state a joint move led to, when that
// needs no search: 1 or 0, or -1 when it has to be searched.
static inline int settle(const MslCoreBrute* brute, const MslBrutePlay* kid,
                         const MslCoreBruteView view[2], int frames,
                         int attacking)
{
    if (kid->outcome != 0) {
        return (kid->outcome > 0) == attacking;
    }
    if (frames - kid->frames <= 0) {
        return !attacking;
    }
    if (kid->attacker < 0 && trapped(brute, view)) {
        return attacking;
    }
    return -1;
}

// The chance a try succeeds per position it costs: trying in falling order of
// this finds a success for the least expected work. An untried move counts as
// an even chance at the cost of one position, so it is tried early.
static inline float record_score(const BruteRecord* record)
{
    return ((float) record->wins + 1.0F) * ((float) record->tries + 1.0F) /
           (((float) record->tries + 2.0F) * ((float) record->positions + 1.0F));
}

static inline void record_add(BruteRecord* record, int won, uint64_t positions)
{
    record->tries += 1;
    record->wins += won != 0;
    record->positions += positions;
}

// Indices of `moves`, best score first; equal scores keep the menu's order.
// `specific` is used for the moves it has seen tried, `general` otherwise.
static inline void order_moves(const int32_t moves[], uint32_t count,
                               const BruteRecord specific[],
                               const BruteRecord general[], uint8_t order[])
{
    float score[MSL_BRUTE_MAX_MENU_MOVES];
    uint32_t i;
    for (i = 0; i < count; ++i) {
        uint32_t id = MOVE_ID(moves[i]);
        float value = record_score(specific != NULL && specific[id].tries != 0
                                       ? &specific[id]
                                       : &general[id]);
        uint32_t j = i;
        while (j > 0 && score[j - 1] < value) {
            score[j] = score[j - 1];
            order[j] = order[j - 1];
            j -= 1;
        }
        score[j] = value;
        order[j] = (uint8_t) i;
    }
}

static int search(MslCoreBrute* brute, int frames, int32_t attacker,
                  int32_t defender, uint32_t level, int attacking)
{
    MslCoreBruteView view[2];
    int32_t attackers[MSL_BRUTE_MAX_MENU_MOVES];
    int32_t defenders[MSL_BRUTE_MAX_MENU_MOVES];
    MslBrutePlay kids[MSL_BRUTE_MAX_MENU_MOVES];
    int8_t settled[MSL_BRUTE_MAX_MENU_MOVES];
    uint8_t mine_order[MSL_BRUTE_MAX_MENU_MOVES];
    uint8_t theirs_order[MSL_BRUTE_MAX_MENU_MOVES];
    const int32_t* mine;
    const int32_t* theirs;
    uint32_t attacker_count;
    uint32_t defender_count;
    uint32_t mine_count;
    uint32_t theirs_count;
    uint32_t i;
    uint64_t key[2];
    uint64_t started = brute->stats.positions;
    int good = attacking ? 1 : -1;

    if (frames <= 0) {
        return !attacking;
    }
    views(brute, view);
    if (attacker < 0 && trapped(brute, view)) {
        return attacking;
    }
    if (level >= brute->level_count || frames >= BRUTE_NO_WIN) {
        brute->failed = 1;
        return 0;
    }
    brute->stats.nodes += 1;
    if (brute->table != NULL) {
        uint64_t moves = ((uint64_t) (uint32_t) attacker << 32 |
                          (uint32_t) defender) ^ brute->salt;
        uint32_t win;
        uint32_t safe;
        state_hash(brute, key);
        key[0] ^= mix64(moves + (uint64_t) attacking, 0x9E3779B97F4A7C15ULL);
        key[1] ^= mix64(moves ^ 0xD1B54A32D192ED03ULL,
                        0xC2B2AE3D27D4EB4FULL + (uint64_t) attacking);
        if (table_find(brute, key, &win, &safe)) {
            if ((uint32_t) frames >= win) {
                brute->stats.table_hits += 1;
                return attacking;
            }
            if ((uint32_t) frames <= safe) {
                brute->stats.table_hits += 1;
                return !attacking;
            }
        }
    }
    options(brute, view, attacker, defender, attackers, &attacker_count,
            defenders, &defender_count);
    if (attacking) {
        mine = attackers;
        mine_count = attacker_count;
        theirs = defenders;
        theirs_count = defender_count;
    } else {
        mine = defenders;
        mine_count = defender_count;
        theirs = attackers;
        theirs_count = attacker_count;
    }
    order_moves(mine, mine_count, NULL, brute->held[attacking], mine_order);
    checkpoint(brute, level);
    for (i = 0; i < mine_count; ++i) {
        int32_t x = mine[mine_order[i]];
        BruteRecord* against = brute->countered[attacking][MOVE_ID(x)];
        uint64_t move_started = brute->stats.positions;
        uint32_t count = 0;
        uint32_t j;
        int refuted = 0;
        order_moves(theirs, theirs_count, against, brute->refuted[attacking],
                    theirs_order);
        // Every answer is looked at first: one that settles the line at once
        // for the other side refutes the move, and the lines that are settled
        // either way need no second look.
        for (j = 0; j < theirs_count && !refuted; ++j) {
            int32_t y = theirs[theirs_order[j]];
            MslCoreBruteView after[2];
            play(brute, attacking ? x : y, attacking ? y : x, &kids[count],
                 after);
            settled[count] = settle(brute, &kids[count], after, frames,
                                    attacking);
            rollback(brute, level);
            if (settled[count] == 0) {
                refuted = 1;
                record_add(&against[MOVE_ID(y)], 1, 1);
                record_add(&brute->refuted[attacking][MOVE_ID(y)], 1, 1);
            }
            count += 1;
        }
        for (j = 0; j < count && !refuted && !brute->failed; ++j) {
            int32_t y = theirs[theirs_order[j]];
            uint64_t answer_started = brute->stats.positions;
            MslCoreBruteView after[2];
            MslBrutePlay again;
            if (settled[j] >= 0) {
                continue;
            }
            play(brute, attacking ? x : y, attacking ? y : x, &again, after);
            refuted = !search(brute, frames - kids[j].frames, kids[j].attacker,
                              kids[j].defender, level + 1, attacking);
            rollback(brute, level);
            record_add(&against[MOVE_ID(y)], refuted,
                       brute->stats.positions - answer_started);
            record_add(&brute->refuted[attacking][MOVE_ID(y)], refuted,
                       brute->stats.positions - answer_started);
        }
        if (brute->failed) {
            return 0;
        }
        record_add(&brute->held[attacking][MOVE_ID(x)], !refuted,
                   brute->stats.positions - move_started);
        if (!refuted) {
            if (brute->table != NULL) {
                table_store(brute, key, frames, attacking,
                            brute->stats.positions - started);
            }
            return 1;
        }
    }
    if (brute->table != NULL) {
        table_store(brute, key, frames, !attacking,
                    brute->stats.positions - started);
    }
    return 0;
}

MslCoreResult msl_core_brute_begin(MslCoreBrute* brute,
                                   const MslBruteRules* rules, void* table,
                                   size_t table_bytes)
{
    MslCoreBruteView view[2];
    uint64_t rules_hash[2];
    uint32_t i;
    if (brute == NULL || rules == NULL || rules->move_count == 0 ||
        rules->move_count > MSL_BRUTE_MAX_MOVES || rules->menu_count == 0 ||
        rules->menu_count > MSL_BRUTE_MAX_MENUS ||
        rules->move_start[rules->move_count] > MSL_BRUTE_MAX_MOVE_FRAMES)
    {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    for (i = 0; i < rules->move_count; ++i) {
        if (rules->move_start[i] >= rules->move_start[i + 1]) {
            return MSL_CORE_INVALID_ARGUMENT;
        }
    }
    for (i = 0; i < rules->menu_count; ++i) {
        uint32_t j;
        if (rules->menu_size[i] == 0 ||
            rules->menu_size[i] > MSL_BRUTE_MAX_MENU_MOVES)
        {
            return MSL_CORE_INVALID_ARGUMENT;
        }
        for (j = 0; j < rules->menu_size[i]; ++j) {
            if (rules->menu_moves[i][j] >= rules->move_count) {
                return MSL_CORE_INVALID_ARGUMENT;
            }
        }
    }
    for (i = 0; i < MSL_BRUTE_ACTIONS; ++i) {
        if (rules->action_menu[i] >= rules->menu_count) {
            return MSL_CORE_INVALID_ARGUMENT;
        }
    }
    brute->rules = *rules;
    brute->has_rules = 1;
    views(brute, view);
    for (i = 0; i < 2; ++i) {
        brute->base_percent[i] = (double) view[i].percent;
        brute->base_stocks[i] = (int) view[i].stocks;
    }
    // Entries from other rules or another base must not answer for these.
    hash_bytes((const uint8_t*) rules, sizeof(*rules), 0, rules_hash);
    brute->salt = rules_hash[0] ^
                  mix64((uint64_t) view[0].stocks << 8 | view[1].stocks,
                        0x9E3779B97F4A7C15ULL);
    memcpy(rules_hash, &view[0].percent, sizeof(float));
    memcpy((uint8_t*) rules_hash + 4, &view[1].percent, sizeof(float));
    brute->salt ^= mix64(rules_hash[0], 0xD1B54A32D192ED03ULL);
    brute->table = NULL;
    brute->table_mask = 0;
    if (table != NULL) {
        uint64_t buckets = 1;
        size_t bucket_bytes = sizeof(BruteEntry) * BRUTE_TABLE_WAYS;
        if (table_bytes < bucket_bytes || ((uintptr_t) table & 15u) != 0) {
            return MSL_CORE_INVALID_ARGUMENT;
        }
        while (buckets * 2 * bucket_bytes <= table_bytes) {
            buckets *= 2;
        }
        brute->table = table;
        brute->table_mask = buckets - 1;
    }
    brute->failed = 0;
    memset(&brute->stats, 0, sizeof(brute->stats));
    memset(brute->held, 0, sizeof(brute->held));
    memset(brute->refuted, 0, sizeof(brute->refuted));
    memset(brute->countered, 0, sizeof(brute->countered));
    return MSL_CORE_OK;
}

MslCoreResult msl_core_brute_play(MslCoreBrute* brute, int32_t attacker,
                                  int32_t defender, MslBrutePlay* out)
{
    MslCoreBruteView view[2];
    if (brute == NULL || out == NULL || !brute->has_rules ||
        !valid_move(brute, attacker) || !valid_move(brute, defender))
    {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    play(brute, attacker, defender, out, view);
    return brute->failed ? MSL_CORE_INVALID_STATE : MSL_CORE_OK;
}

MslCoreResult msl_core_brute_options(MslCoreBrute* brute, int32_t attacker,
                                     int32_t defender, int32_t attackers[],
                                     uint32_t* attacker_count,
                                     int32_t defenders[],
                                     uint32_t* defender_count)
{
    MslCoreBruteView view[2];
    if (brute == NULL || attackers == NULL || attacker_count == NULL ||
        defenders == NULL || defender_count == NULL || !brute->has_rules ||
        (attacker >= 0 && !valid_move(brute, attacker)) ||
        (defender >= 0 && !valid_move(brute, defender)))
    {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    views(brute, view);
    options(brute, view, attacker, defender, attackers, attacker_count,
            defenders, defender_count);
    return MSL_CORE_OK;
}

MslCoreResult msl_core_brute_trapped(MslCoreBrute* brute, int32_t* out_trapped)
{
    MslCoreBruteView view[2];
    if (brute == NULL || out_trapped == NULL || !brute->has_rules) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    views(brute, view);
    *out_trapped = trapped(brute, view);
    return MSL_CORE_OK;
}

MslCoreResult msl_core_brute_search(MslCoreBrute* brute, int32_t frames,
                                    int32_t attacker, int32_t defender,
                                    uint32_t level, int attacking,
                                    int32_t* out_holds)
{
    if (brute == NULL || out_holds == NULL || !brute->has_rules ||
        (attacker >= 0 && !valid_move(brute, attacker)) ||
        (defender >= 0 && !valid_move(brute, defender)))
    {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    *out_holds = search(brute, frames, attacker, defender, level,
                        attacking != 0);
    return brute->failed ? MSL_CORE_INVALID_STATE : MSL_CORE_OK;
}

MslCoreResult msl_core_brute_stats(const MslCoreBrute* brute,
                                   MslBruteStats* out)
{
    if (brute == NULL || out == NULL) {
        return MSL_CORE_INVALID_ARGUMENT;
    }
    *out = brute->stats;
    out->dirty_pages = brute->dirty_count;
    return MSL_CORE_OK;
}

#else

MslCoreResult msl_core_brute_create(MslCoreBatch* batch, uint32_t match_index,
                                    uint32_t level_count,
                                    MslCoreBrute** out_brute)
{
    (void) batch;
    (void) match_index;
    (void) level_count;
    (void) out_brute;
    return MSL_CORE_INVALID_STATE;
}

void msl_core_brute_destroy(MslCoreBrute* brute)
{
    (void) brute;
}

MslCoreResult msl_core_brute_reset(MslCoreBrute* brute)
{
    (void) brute;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_checkpoint(MslCoreBrute* brute, uint32_t level)
{
    (void) brute;
    (void) level;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_rollback(MslCoreBrute* brute, uint32_t level)
{
    (void) brute;
    (void) level;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_hash(MslCoreBrute* brute, uint64_t hash[2])
{
    (void) brute;
    (void) hash;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_step(MslCoreBrute* brute, const MslInput* input)
{
    (void) brute;
    (void) input;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_begin(MslCoreBrute* brute,
                                   const MslBruteRules* rules, void* table,
                                   size_t table_bytes)
{
    (void) brute;
    (void) rules;
    (void) table;
    (void) table_bytes;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_play(MslCoreBrute* brute, int32_t attacker,
                                  int32_t defender, MslBrutePlay* out)
{
    (void) brute;
    (void) attacker;
    (void) defender;
    (void) out;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_options(MslCoreBrute* brute, int32_t attacker,
                                     int32_t defender, int32_t attackers[],
                                     uint32_t* attacker_count,
                                     int32_t defenders[],
                                     uint32_t* defender_count)
{
    (void) brute;
    (void) attacker;
    (void) defender;
    (void) attackers;
    (void) attacker_count;
    (void) defenders;
    (void) defender_count;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_trapped(MslCoreBrute* brute, int32_t* out_trapped)
{
    (void) brute;
    (void) out_trapped;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_search(MslCoreBrute* brute, int32_t frames,
                                    int32_t attacker, int32_t defender,
                                    uint32_t level, int attacking,
                                    int32_t* out_holds)
{
    (void) brute;
    (void) frames;
    (void) attacker;
    (void) defender;
    (void) level;
    (void) attacking;
    (void) out_holds;
    return MSL_CORE_INVALID_STATE;
}

MslCoreResult msl_core_brute_stats(const MslCoreBrute* brute,
                                   MslBruteStats* out)
{
    (void) brute;
    (void) out;
    return MSL_CORE_INVALID_STATE;
}

#endif
