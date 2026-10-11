# -*- coding: utf-8 -*-
"""
Created on Thu Apr 11 00:01:36 2024

@author: jakob
"""
import math
import chess.polyglot
import nnue
from transpositionTable import TranspositionTable

BISHOP_PAIR = 30
KNIGHT_PAIR = 8
ROOK_PAIR = 16
KNIGHT_TRAPPED_A7 = 100
KNIGHT_TRAPPED_A8 = 150
KNIGHT_C3 = 5
BISHOP_TRAPPED_A6 = 50
BISHOP_TRAPPED_A7 = 150
RETURNING_BISHOP = 20
ROOK_OPEN = 20
ROOK_HALF_OPEN = 10
SHIELD1 = 10
SHIELD2 = 5
TEMPO = 10
KING_BLOCKS_ROOK = 40
BLOCK_CENTRAL_PAWN = 30
DOUBLED_PAWN = 25
PROTECTED_PASSER = [0, 25, 25, 50, 75, 100, 125, 0]
PASSED_PAWN = [0, 20, 20, 40, 60, 80, 100, 0]
WEAK_PAWN = [10, 12, 14, 16, 16, 14, 12, 10]

NNUE_SCALE = 400  # cp scale

class Evaluator2:
    def __init__(self):
        self.phaseWeight = [0, 1, 1, 2, 4, 0]
        self.positionTable = TranspositionTable(8, 6)

    def lazyEval(self, position, accumulator, who2Move, alpha, beta):
        key = chess.polyglot.zobrist_hash(position)
        entry = self.positionTable.tryLookupEval(key, who2Move)
        if entry:
            return entry.value

        z = nnue.nnue_eval(accumulator)
        val = NNUE_SCALE * z
        val *= who2Move
        self.positionTable.storePositionEval(key, val, who2Move)
        """
        # optional lazy cutoff
        if val <= alpha - 200:
            return val
        if val >= beta + 200:
            return val
        """
        return val

    def phase(self, position):
        phase = 24
        for sq in range(64):
            pc = position.piece_at(sq)
            if pc:
                typ = pc.piece_type - 1
                phase -= self.phaseWeight[typ]
        if phase < 0:
            phase = 0
        phase = (phase*256)/24
        return phase

class Evaluator5:
    def __init__(self):
        self.midGameValueTable = [82, 337, 365, 477, 1025, 0]
        self.endGameValueTable = [94, 281, 297, 512, 936, 0]
        self.phaseWeight = [0, 1, 1, 2, 4, 0]

        mgPawns = [0, 0, 0, 0, 0, 0, 0, 0,
                   -6, -4, 1, 1, 1, 1, -4, -6,
                   -6, -4, 1, 2, 2, 1, -4, -6,
                   -6, -4, 2, 8, 8, 2, -4, -6,
                   -6, -4, 5, 10, 10, 5, -4, -6,
                   -4, -4, 1, 5, 5, 1, -4, -4,
                   -6, -4, 1, -24, -24, 1, -4, -6,
                   0, 0, 0, 0, 0, 0, 0, 0]
        egPawns = [0, 0, 0, 0, 0, 0, 0, 0,
                   10, 10, 12, 12, 12, 12, 10, 10,
                   -6, -4, 1, 2, 2, 1, -4, -6,
                   -6, -4, 2, 8, 8, 2, -4, -6,
                   -6, -4, 5, 10, 10, 5, -4, -6,
                   -4, -4, 1, 5, 5, 1, -4, -4,
                   -6, -4, 1, -24, -24, 1, -4, -6,
                   0, 0, 0, 0, 0, 0, 0, 0]
        mgKnights = [-8, -8, -8, -8, -8, -8, -8, -8,
                     -8, 0, 0, 0, 0, 0, 0, -8,
                     -8, 0, 4, 4, 4, 4, 0, -8,
                     -8, 0, 4, 8, 8, 4, 0, -8,
                     -8, 0, 4, 8, 8, 4, 0, -8,
                     -8, 0, 4, 4, 4, 4, 0, -8,
                     -8, 0, 1, 2, 2, 1, 0, -8,
                     -8, -12, -8, -8, -8, -8, -12, -8]
        egKnights = [-8, -8, -8, -8, -8, -8, -8, -8,
                     -8, 0, 0, 0, 0, 0, 0, -8,
                     -8, 0, 4, 4, 4, 4, 0, -8,
                     -8, 0, 4, 8, 8, 4, 0, -8,
                     -8, 0, 4, 8, 8, 4, 0, -8,
                     -8, 0, 4, 4, 4, 4, 0, -8,
                     -8, 0, 1, 2, 2, 1, 0, -8,
                     -8, -12, -8, -8, -8, -8, -12, -8]
        mgBishops = [-4, -4, -4, -4, -4, -4, -4, -4,
                     -4, 0, 0, 0, 0, 0, 0, -4,
                     -4, 0, 2, 4, 4, 2, 0, -4,
                     -4, 0, 4, 6, 6, 4, 0, -4,
                     -4, 0, 4, 6, 6, 4, 0, -4,
                     -4, 1, 2, 4, 4, 2, 1, -4,
                     -4, 2, 1, 1, 1, 1, 2, -4,
                     -4, -4, -12, -4, -4, -12, -4, -4]
        egBishops = [-4, -4, -4, -4, -4, -4, -4, -4,
                     -4, 0, 0, 0, 0, 0, 0, -4,
                     -4, 0, 2, 4, 4, 2, 0, -4,
                     -4, 0, 4, 6, 6, 4, 0, -4,
                     -4, 0, 4, 6, 6, 4, 0, -4,
                     -4, 1, 2, 4, 4, 2, 1, -4,
                     -4, 2, 1, 1, 1, 1, 2, -4,
                     -4, -4, -12, -4, -4, -12, -4, -4]
        mgRooks = [5, 5, 5, 5, 5, 5, 5, 5,
                   20, 20, 20, 20, 20, 20, 20, 20,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   0, 0, 0, 2, 2, 0, 0, 0]
        egRooks = [5, 5, 5, 5, 5, 5, 5, 5,
                   20, 20, 20, 20, 20, 20, 20, 20,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   -5, 0, 0, 0, 0, 0, 0, -5,
                   0, 0, 0, 2, 2, 0, 0, 0]
        mgQueens = [0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 1, 1, 1, 1, 0, 0,
                    0, 0, 1, 2, 2, 1, 0, 0,
                    0, 0, 2, 3, 3, 2, 0, 0,
                    0, 0, 2, 3, 3, 2, 0, 0,
                    0, 0, 1, 2, 2, 1, 0, 0,
                    0, 0, 1, 1, 1, 1, 0, 0,
                    -5, -5, -5, -5, -5, -5, -5, -5]
        egQueens = [0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 1, 1, 1, 1, 0, 0,
                    0, 0, 1, 2, 2, 1, 0, 0,
                    0, 0, 2, 3, 3, 2, 0, 0,
                    0, 0, 2, 3, 3, 2, 0, 0,
                    0, 0, 1, 2, 2, 1, 0, 0,
                    0, 0, 1, 1, 1, 1, 0, 0,
                    -5, -5, -5, -5, -5, -5, -5, -5]
        mgKing = [-40, -30, -50, -70, -70, -50, -30, -40,
                  -30, -20, -40, -60, -60, -40, -20, -30,
                  -20, -10, -30, -50, -50, -30, -10, -20,
                  -10, 0, -20, -40, -40, -20, 0, -10,
                  0, 10, -10, -30, -30, -10, 10, 0,
                  10, 20, 0, -20, -20, 0, 20, 10,
                  30, 40, 20, 0, 0, 20, 40, 30,
                  40, 50, 30, 10, 10, 30, 50, 40]
        egKing = [-72, -48, -36, -24, -24, -36, -48, -72,
                  -48, -24, -12, 0, 0, -12, -24, -48,
                  -36, -12, 0, 12, 12, 0, -12, -36,
                  -24, 0, 12, 24, 24, 12, 0, -24,
                  -24, 0, 12, 24, 24, 12, 0, -24,
                  -36, -12, 0, 12, 12, 0, -12, -36,
                  -48, -24, -12, 0, 0, -12, -24, -48,
                  -72, -48, -36, -24, -24, -36, -48, -72]

        self.mgTable = [mgPawns, mgKnights, mgBishops, mgRooks, mgQueens, mgKing]
        self.egTable = [egPawns, egKnights, egBishops, egRooks, egQueens, egKing]
        for i in range(6):
            for j in range(64):
                self.mgTable[i][j] += self.midGameValueTable[i]
                self.egTable[i][j] += self.endGameValueTable[i]

        self.sqw = []
        self.sqb = []
        for i in range(64):
            sqbr = math.floor(i / 8)
            self.sqw.append((7 - sqbr) * 8 + (i % 8))
            self.sqb.append(sqbr * 8 + 7 - (i % 8))

        self.positionTable = TranspositionTable(8, 6)
        self.gamePhase = 0

        self.Wcount = [0, 0, 0, 0, 0]
        self.Bcount = [0, 0, 0, 0, 0]

    def lazyEval(self, position, accumulator, who2Move, alpha, beta):
        key = chess.polyglot.zobrist_hash(position)
        entry = self.positionTable.tryLookupEval(key, who2Move)
        if entry:
            return entry.value

        self.Wcount = [0, 0, 0, 0, 0]
        self.Bcount = [0, 0, 0, 0, 0]
        self.gamePhase = 0

        squareSetWhiteAll = []
        squareSetBlackAll = []

        mgScore = 0
        egScore = 0

        for typ in range(5):
            squareSetWhite = position.pieces(typ + 1, 1)
            squareSetBlack = position.pieces(typ + 1, 0)

            squareSetWhiteAll.append(squareSetWhite)
            squareSetBlackAll.append(squareSetBlack)

            self.Wcount[typ] = len(squareSetWhite)
            self.Bcount[typ] = len(squareSetBlack)
            for sq in squareSetWhite:
                sqw = self.sqw[sq]
                mgScore += self.mgTable[typ][sqw]
                egScore += self.egTable[typ][sqw]
                self.gamePhase += self.phaseWeight[typ]
            for sq in squareSetBlack:
                sqb = self.sqw[sq]
                mgScore -= self.mgTable[typ][sqb]
                egScore -= self.egTable[typ][sqb]
                self.gamePhase += self.phaseWeight[typ]

        # try:
        kingWSq = position.king(1)
        kingBSq = position.king(0)

        kingW = self.sqw[kingWSq]
        kingB = self.sqb[kingBSq]

        mgScore += self.mgTable[5][kingW]
        egScore += self.egTable[5][kingW]
        mgScore -= self.mgTable[5][kingB]
        egScore -= self.egTable[5][kingB]

        if self.gamePhase > 24:
            self.gamePhase = 24

        evaluation = ((mgScore * self.gamePhase) + (egScore * (24 - self.gamePhase))) / 24
        evaluation *= who2Move
        if evaluation > beta * 1.3 or evaluation < alpha * 0.9:
            # TODO: dont return only evaluation, but also info about cutoff (reevaluate possible later)
            # self.positionTable.storePositionEval(key, evaluation, who2Move)
            return evaluation

        z = nnue.nnue_eval(accumulator)
        val = NNUE_SCALE * z
        val *= who2Move

        result = (evaluation + val) / 2
        self.positionTable.storePositionEval(key, result, who2Move)
        return result

    def phase(self, position):
        phase = 24
        for sq in range(64):
            pc = position.piece_at(sq)
            if pc:
                typ = pc.piece_type - 1
                phase -= self.phaseWeight[typ]
        if phase < 0:
            phase = 0
        phase = (phase*256)/24
        return phase


#TODO: maybe use material hash table (maybe even precomputed)
class Evaluator:
    def __init__(self):
        self.midGameValueTable = [82, 337, 365, 477, 1025,  0]
        self.endGameValueTable = [94, 281, 297, 512,  936,  0]
        self.phaseWeight = [0, 1, 1, 2, 4, 0]

        mgPawns = [0,   0,   0,   0,   0,   0,   0,   0,
  -6,  -4,   1,   1,   1,   1,  -4,  -6,
  -6,  -4,   1,   2,   2,   1,  -4,  -6,
  -6,  -4,   2,   8,   8,   2,  -4,  -6,
  -6,  -4,   5,  10,  10,   5,  -4,  -6,
  -4,  -4,   1,   5,   5,   1,  -4,  -4,
  -6,  -4,   1, -24,  -24,  1,  -4,  -6,
  0,   0,   0,   0,   0,   0,   0,   0]
        egPawns = [0,   0,   0,   0,   0,   0,   0,   0,
  10,  10,   12,   12,   12,   12,  10,  10,
  -6,  -4,   1,   2,   2,   1,  -4,  -6,
  -6,  -4,   2,   8,   8,   2,  -4,  -6,
  -6,  -4,   5,  10,  10,   5,  -4,  -6,
  -4,  -4,   1,   5,   5,   1,  -4,  -4,
  -6,  -4,   1, -24,  -24,  1,  -4,  -6,
  0,   0,   0,   0,   0,   0,   0,   0]
        mgKnights = [-8,  -8,  -8,  -8,  -8,  -8,  -8,  -8,
  -8,   0,   0,   0,   0,   0,   0,  -8,
  -8,   0,   4,   4,   4,   4,   0,  -8,
  -8,   0,   4,   8,   8,   4,   0,  -8,
  -8,   0,   4,   8,   8,   4,   0,  -8,
  -8,   0,   4,   4,   4,   4,   0,  -8,
  -8,   0,   1,   2,   2,   1,   0,  -8,
  -8, -12,  -8,  -8,  -8,  -8, -12,  -8]
        egKnights = [-8,  -8,  -8,  -8,  -8,  -8,  -8,  -8,
  -8,   0,   0,   0,   0,   0,   0,  -8,
  -8,   0,   4,   4,   4,   4,   0,  -8,
  -8,   0,   4,   8,   8,   4,   0,  -8,
  -8,   0,   4,   8,   8,   4,   0,  -8,
  -8,   0,   4,   4,   4,   4,   0,  -8,
  -8,   0,   1,   2,   2,   1,   0,  -8,
  -8, -12,  -8,  -8,  -8,  -8, -12,  -8]
        mgBishops = [-4,  -4,  -4,  -4,  -4,  -4,  -4,  -4,
  -4,   0,   0,   0,   0,   0,   0,  -4,
  -4,   0,   2,   4,   4,   2,   0,  -4,
  -4,   0,   4,   6,   6,   4,   0,  -4,
  -4,   0,   4,   6,   6,   4,   0,  -4,
  -4,   1,   2,   4,   4,   2,   1,  -4,
  -4,   2,   1,   1,   1,   1,   2,  -4,
  -4,  -4, -12,  -4,  -4, -12,  -4,  -4]
        egBishops = [-4,  -4,  -4,  -4,  -4,  -4,  -4,  -4,
  -4,   0,   0,   0,   0,   0,   0,  -4,
  -4,   0,   2,   4,   4,   2,   0,  -4,
  -4,   0,   4,   6,   6,   4,   0,  -4,
  -4,   0,   4,   6,   6,   4,   0,  -4,
  -4,   1,   2,   4,   4,   2,   1,  -4,
  -4,   2,   1,   1,   1,   1,   2,  -4,
  -4,  -4, -12,  -4,  -4, -12,  -4,  -4]
        mgRooks = [5,   5,   5,   5,   5,   5,   5,   5,
  20,  20,  20,  20,  20,  20,  20,  20,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  0,   0,   0,   2,   2,   0,   0,   0]
        egRooks = [5,   5,   5,   5,   5,   5,   5,   5,
  20,  20,  20,  20,  20,  20,  20,  20,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  -5,   0,   0,   0,   0,   0,   0,  -5,
  0,   0,   0,   2,   2,   0,   0,   0]
        mgQueens = [0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 1, 1, 1, 1, 0, 0,
  0, 0, 1, 2, 2, 1, 0, 0,
  0, 0, 2, 3, 3, 2, 0, 0,
  0, 0, 2, 3, 3, 2, 0, 0,
  0, 0, 1, 2, 2, 1, 0, 0,
  0, 0, 1, 1, 1, 1, 0, 0,
  -5, -5, -5, -5, -5, -5, -5, -5]
        egQueens = [0, 0, 0, 0, 0, 0, 0, 0,
  0, 0, 1, 1, 1, 1, 0, 0,
  0, 0, 1, 2, 2, 1, 0, 0,
  0, 0, 2, 3, 3, 2, 0, 0,
  0, 0, 2, 3, 3, 2, 0, 0,
  0, 0, 1, 2, 2, 1, 0, 0,
  0, 0, 1, 1, 1, 1, 0, 0,
  -5, -5, -5, -5, -5, -5, -5, -5]
        mgKing = [-40, -30, -50, -70, -70, -50, -30, -40,
  -30, -20, -40, -60, -60, -40, -20, -30,
  -20, -10, -30, -50, -50, -30, -10, -20,
  -10,   0, -20, -40, -40, -20,   0, -10,
  0,  10, -10, -30, -30, -10,  10,   0,
  10,  20,   0, -20, -20,   0,  20,  10,
  30,  40,  20,   0,   0,  20,  40,  30,
  40,  50,  30,  10,  10,  30,  50,  40]
        egKing = [-72, -48, -36, -24, -24, -36, -48, -72,
  -48, -24, -12,   0,   0, -12, -24, -48,
  -36, -12,   0,  12,  12,   0, -12, -36,
  -24,   0,  12,  24,  24,  12,   0, -24,
  -24,   0,  12,  24,  24,  12,   0, -24,
  -36, -12,   0,  12,  12,   0, -12, -36,
  -48, -24, -12,   0,   0, -12, -24, -48,
  -72, -48, -36, -24, -24, -36, -48, -72]

        self.mgTable = [mgPawns, mgKnights, mgBishops, mgRooks, mgQueens, mgKing]
        self.egTable = [egPawns, egKnights, egBishops, egRooks, egQueens, egKing]
        for i in range(6):
            for j in range(64):
                self.mgTable[i][j] += self.midGameValueTable[i]
                self.egTable[i][j] += self.endGameValueTable[i]

        self.sqw = []
        self.sqb = []
        for i in range(64):
            sqbr = math.floor(i / 8)
            self.sqw.append((7 - sqbr) * 8 + (i % 8))
            self.sqb.append(sqbr * 8 + 7 - (i % 8))


        self.positionTable = TranspositionTable(8, 6)
        self.gamePhase = 0
        self.blockagesWHITE = 0
        self.blockagesBLACK = 0
        self.materialAdjWHITE = 0
        self.materialAdjBLACK = 0
        self.positionalThemesWHITE = 0
        self.positionalThemesBLACK = 0
        self.mgMobWHITE = 0
        self.egMobWHITE = 0
        self.mgMobBLACK = 0
        self.egMobBLACK = 0
        self.attCountWHITE = 0
        self.attWeightWHITE = 0
        self.attCountBLACK = 0
        self.attWeightBLACK = 0

        self.Wcount = [0, 0, 0, 0, 0]
        self.Bcount = [0, 0, 0, 0, 0]

        self.knight_adj = [-20, -16, -12, -8, -4,  0,  4,  8, 12]
        self.rook_adj = [15,  12,   9,  6,  3,  0, -3, -6, -9]
        self.safetyTable = [0,  0,   1,   2,   3,   5,   7,   9,  12,  15,
                      18,  22,  26,  30,  35,  39,  44,  50,  56,  62,
                      68,  75,  82,  85,  89,  97, 105, 113, 122, 131,
                      140, 150, 169, 180, 191, 202, 213, 225, 237, 248,
                      260, 272, 283, 295, 307, 319, 330, 342, 354, 366,
                      377, 389, 401, 412, 424, 436, 448, 459, 471, 483,
                      494, 500, 500, 500, 500, 500, 500, 500, 500, 500,
                      500, 500, 500, 500, 500, 500, 500, 500, 500, 500,
                      500, 500, 500, 500, 500, 500, 500, 500, 500, 500,
                      500, 500, 500, 500, 500, 500, 500, 500, 500, 500]

    def lazyEval(self, position, accumulator, who2Move, alpha, beta):
        result = 0
        pieceMatWhite = 0
        pieceMatBlack = 0
        key = chess.polyglot.zobrist_hash(position)
        entry = self.positionTable.tryLookupEval(key, who2Move)
        if entry:
            return entry.value

        self.gamePhase = 0
        self.blockagesWHITE = 0
        self.blockagesBLACK = 0
        self.materialAdjWHITE = 0
        self.materialAdjBLACK = 0
        self.positionalThemesWHITE = 0
        self.positionalThemesBLACK = 0
        self.mgMobWHITE = 0
        self.egMobWHITE = 0
        self.mgMobBLACK = 0
        self.egMobBLACK = 0
        self.attCountWHITE = 0
        self.attWeightWHITE = 0
        self.attCountBLACK = 0
        self.attWeightBLACK = 0

        self.Wcount = [0, 0, 0, 0, 0]
        self.Bcount = [0, 0, 0, 0, 0]

        self.sqNearKingWhite = []
        self.sqNearKingBlack = []

        squareSetWhiteAll = []
        squareSetBlackAll = []

        mgScore = 0
        egScore = 0

        for typ in range(5):
            squareSetWhite = position.pieces(typ+1, 1)
            squareSetBlack = position.pieces(typ+1, 0)

            squareSetWhiteAll.append(squareSetWhite)
            squareSetBlackAll.append(squareSetBlack)

            self.Wcount[typ] = len(squareSetWhite)
            self.Bcount[typ] = len(squareSetBlack)
            for sq in squareSetWhite:
                sqw = self.sqw[sq]
                mgScore += self.mgTable[typ][sqw]
                egScore += self.egTable[typ][sqw]
                self.gamePhase += self.phaseWeight[typ]
                pieceMatWhite += self.endGameValueTable[typ]
            for sq in squareSetBlack:
                sqb = self.sqw[sq]
                mgScore -= self.mgTable[typ][sqb]
                egScore -= self.egTable[typ][sqb]
                self.gamePhase += self.phaseWeight[typ]
                pieceMatBlack += self.endGameValueTable[typ]

        #try:
        kingWSq = position.king(1)
        kingBSq = position.king(0)

        kingW = self.sqw[kingWSq]
        kingB = self.sqb[kingBSq]

        mgScore += self.mgTable[5][kingW]
        egScore += self.egTable[5][kingW]
        mgScore -= self.mgTable[5][kingB]
        egScore -= self.egTable[5][kingB]

        kingShieldWHITE, kingShieldBLACK = self.kingShield(position, kingWSq, kingBSq)
        mgScore += (kingShieldWHITE - kingShieldBLACK)
        #except:
            #pass

        if self.gamePhase > 24:
            self.gamePhase = 24

        evaluation = ((mgScore * self.gamePhase) + (egScore * (24 - self.gamePhase))) / 24
        evaluation *= who2Move
        if evaluation > beta*1.3 or evaluation < alpha*0.9:
            #TODO: dont return only evaluation, but also info about cutoff (reevaluate possible later)
            #self.positionTable.storePositionEval(key, evaluation, who2Move)
            return evaluation

        self.blockedPieces(position)

        if who2Move == 1:
            result += TEMPO
        else:
            result -= TEMPO

        result += self.evalPawnStructure(squareSetWhiteAll[0], squareSetBlackAll[0], position)

        if self.Wcount[2] > 1:
            result += BISHOP_PAIR
        if self.Bcount[2] > 1:
            result -= BISHOP_PAIR
        if self.Wcount[1] > 1:
            result -= KNIGHT_PAIR
        if self.Bcount[1] > 1:
            result += KNIGHT_PAIR
        if self.Wcount[3] > 1:
            result -= ROOK_PAIR
        if self.Bcount[3] > 1:
            result += ROOK_PAIR

        for count, el in enumerate(squareSetWhiteAll):
            match count:
                case 0:
                    pass
                case 1:
                    for pc in el:
                        self.evalKnight(pc, 1, position)
                case 2:
                    for pc in el:
                        self.evalBishop(pc, 1, position)
                case 3:
                    for pc in el:
                        self.evalRook(pc, 1, position)
                case 4:
                    for pc in el:
                        self.evalQueen(pc, 1, position)

        for count, el in enumerate(squareSetBlackAll):
            match count:
                case 0:
                    pass
                case 1:
                    for pc in el:
                        self.evalKnight(pc, 0, position)
                case 2:
                    for pc in el:
                        self.evalBishop(pc, 0, position)
                case 3:
                    for pc in el:
                        self.evalRook(pc, 0, position)
                case 4:
                    for pc in el:
                        self.evalQueen(pc, 0, position)

        mgScore += (self.mgMobWHITE - self.mgMobBLACK)
        egScore += (self.egMobWHITE - self.egMobBLACK)

        result += ((mgScore * self.gamePhase) + (egScore * (24 - self.gamePhase))) / 24

        result += (self.blockagesWHITE - self.blockagesBLACK)
        result += (self.positionalThemesWHITE - self.positionalThemesBLACK)
        result += (self.materialAdjWHITE - self.materialAdjBLACK)

        if self.attCountWHITE >= 2 and self.Wcount[4] != 0:
            result += self.safetyTable[self.attWeightWHITE]
        if self.attCountBLACK >= 2 and self.Bcount[4] != 0:
            result -= self.safetyTable[self.attWeightBLACK]

        #adjustments for low material (some endgames are draw or drawish)
        if result > 0:
            if self.Wcount[0] == 0:
                if pieceMatWhite < 400:
                    self.positionTable.storePositionEval(key, 0, who2Move)
                    return 0
                if self.Bcount[0] == 0 and pieceMatWhite == 2*self.endGameValueTable[1]:
                    self.positionTable.storePositionEval(key, 0, who2Move)
                    return 0
                if pieceMatWhite == self.endGameValueTable[3] and pieceMatBlack == self.endGameValueTable[2]:
                    result /= 2
                elif pieceMatWhite == self.endGameValueTable[3] and pieceMatBlack == self.endGameValueTable[1]:
                    result /= 2
                elif pieceMatWhite == self.endGameValueTable[3] + self.endGameValueTable[2] and pieceMatBlack == self.endGameValueTable[3]:
                    result /= 2
                elif pieceMatWhite == self.endGameValueTable[3] + self.endGameValueTable[1] and pieceMatBlack == self.endGameValueTable[3]:
                    result /= 2
        else:
            if self.Bcount[0] == 0:
                if pieceMatBlack < 400:
                    self.positionTable.storePositionEval(key, 0, who2Move)
                    return 0
                if self.Wcount[0] == 0 and pieceMatBlack == 2*self.endGameValueTable[1]:
                    self.positionTable.storePositionEval(key, 0, who2Move)
                    return 0
                if pieceMatBlack == self.endGameValueTable[3] and pieceMatWhite == self.endGameValueTable[2]:
                    result /= 2
                elif pieceMatBlack == self.endGameValueTable[3] and pieceMatWhite == self.endGameValueTable[1]:
                    result /= 2
                elif pieceMatBlack == self.endGameValueTable[3] + self.endGameValueTable[2] and pieceMatWhite == self.endGameValueTable[3]:
                    result /= 2
                elif pieceMatBlack == self.endGameValueTable[3] + self.endGameValueTable[1] and pieceMatWhite == self.endGameValueTable[3]:
                    result /= 2

        # result relative to side to move
        result = result * who2Move

        self.positionTable.storePositionEval(key, result, who2Move)
        return result
                
    def evalKnight(self, sq, color, position):
        if color:
            self.materialAdjWHITE += self.knight_adj[self.Wcount[0]]
            match sq:
                case 56:
                    if (position.piece_type_at(48) == 1 and position.color_at(48) == 0) or (position.piece_type_at(50) == 1 and position.color_at(50) == 0):
                        self.blockagesWHITE -= KNIGHT_TRAPPED_A8
                case 63:
                    if (position.piece_type_at(55) == 1 and position.color_at(55) == 0) or (position.piece_type_at(53) == 1 and position.color_at(53) == 0):
                        self.blockagesWHITE -= KNIGHT_TRAPPED_A8
                case 48:
                    if (position.piece_type_at(40) == 1 and position.color_at(40) == 0) and (position.piece_type_at(49) == 1 and position.color_at(49) == 0):
                        self.blockagesWHITE -= KNIGHT_TRAPPED_A7
                case 55:
                    if (position.piece_type_at(54) == 1 and position.color_at(54) == 0) and (position.piece_type_at(47) == 1 and position.color_at(47) == 0):
                        self.blockagesWHITE -= KNIGHT_TRAPPED_A7
                case 18:
                    if (position.piece_type_at(10) == 1 and position.color_at(10) == 1) and (position.piece_type_at(27) == 1 and position.color_at(27) == 1) and not(position.piece_type_at(28) == 1 and position.color_at(28) == 1):
                        self.blockagesWHITE -= KNIGHT_C3
        else:
            self.materialAdjBLACK += self.knight_adj[self.Bcount[0]]
            match sq:
                case 0:
                    if (position.piece_type_at(8) == 1 and position.color_at(8) == 1) or (position.piece_type_at(10) == 1 and position.color_at(10) == 1):
                        self.blockagesBLACK -= KNIGHT_TRAPPED_A8
                case 7:
                    if (position.piece_type_at(13) == 1 and position.color_at(13) == 1) or (position.piece_type_at(15) == 1 and position.color_at(15) == 1):
                        self.blockagesBLACK -= KNIGHT_TRAPPED_A8
                case 8:
                    if (position.piece_type_at(16) == 1 and position.color_at(16) == 1) and (position.piece_type_at(9) == 1 and position.color_at(9) == 1):
                        self.blockagesBLACK -= KNIGHT_TRAPPED_A7
                case 15:
                    if (position.piece_type_at(14) == 1 and position.color_at(14) == 1) and (position.piece_type_at(23) == 1 and position.color_at(23) == 1):
                        self.blockagesBLACK -= KNIGHT_TRAPPED_A7
                case 42:
                    if (position.piece_type_at(50) == 1 and position.color_at(50) == 0) and (position.piece_type_at(35) == 1 and position.color_at(35) == 0) and not(position.piece_type_at(36) == 1 and position.color_at(36) == 0):
                        self.blockagesBLACK -= KNIGHT_C3

        #mobility and KingAttacks
        dirs = [17, 10, -6, -15, -17, -10, 6, 15]
        mob = 0
        att = 0
        sqNew = sq
        for i in range(2):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[i]
            if sqNew % 8 > sqRest and sqNew < 64 and position.color_at(sqNew) != color:
                mob += 1
                if self.sqNearK(not color, sqNew):
                    att += 1
        sqNew = sq
        for i in range(2,4):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[i]
            if sqNew % 8 > sqRest and sqNew >= 0 and position.color_at(sqNew) != color:
                mob += 1
                if self.sqNearK(not color, sqNew):
                    att += 1
        sqNew = sq
        for i in range(4,6):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[i]
            if sqNew % 8 < sqRest and sqNew >= 0 and position.color_at(sqNew) != color:
                mob += 1
                if self.sqNearK(not color, sqNew):
                    att += 1
        sqNew = sq
        for i in range(6,8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[i]
            if sqNew % 8 < sqRest and sqNew < 64 and position.color_at(sqNew) != color:
                mob += 1
                if self.sqNearK(not color, sqNew):
                    att += 1

        if color:
            self.mgMobWHITE += 4*(mob-4)
            self.egMobWHITE += 4 * (mob - 4)
            if att:
                self.attCountWHITE += 1
                self.attWeightWHITE += 2 * att
        else:
            self.mgMobBLACK += 4 * (mob - 4)
            self.egMobBLACK += 4 * (mob - 4)
            if att:
                self.attCountBLACK += 1
                self.attWeightBLACK += 2 * att


    def evalBishop(self, sq, color, position):
        if color:
            match sq:
                case 48:
                    if position.piece_type_at(41) == 1 and position.color_at(41) == 0:
                        self.blockagesWHITE -= BISHOP_TRAPPED_A7
                case 55:
                    if position.piece_type_at(46) == 1 and position.color_at(46) == 0:
                        self.blockagesWHITE -= BISHOP_TRAPPED_A7
                case 57:
                    if position.piece_type_at(50) == 1 and position.color_at(50) == 0:
                        self.blockagesWHITE -= BISHOP_TRAPPED_A7
                case 62:
                    if position.piece_type_at(53) == 1 and position.color_at(53) == 0:
                        self.blockagesWHITE -= BISHOP_TRAPPED_A7
                case 40:
                    if position.piece_type_at(33) == 1 and position.color_at(33) == 0:
                        self.blockagesWHITE -= BISHOP_TRAPPED_A6
                case 47:
                    if position.piece_type_at(38) == 1 and position.color_at(38) == 0:
                        self.blockagesWHITE -= BISHOP_TRAPPED_A6
                case 5:
                    if position.piece_type_at(6) == 6 and position.color_at(6) == 1:
                        self.positionalThemesWHITE += RETURNING_BISHOP
                case 2:
                    if position.piece_type_at(1) == 6 and position.color_at(1) == 1:
                        self.positionalThemesWHITE += RETURNING_BISHOP
        else:
            match sq:
                case 8:
                    if position.piece_type_at(17) == 1 and position.color_at(17) == 1:
                        self.blockagesBLACK -= BISHOP_TRAPPED_A7
                case 15:
                    if position.piece_type_at(22) == 1 and position.color_at(22) == 1:
                        self.blockagesBLACK -= BISHOP_TRAPPED_A7
                case 1:
                    if position.piece_type_at(10) == 1 and position.color_at(10) == 1:
                        self.blockagesBLACK -= BISHOP_TRAPPED_A7
                case 6:
                    if position.piece_type_at(13) == 1 and position.color_at(13) == 1:
                        self.blockagesBLACK -= BISHOP_TRAPPED_A7
                case 16:
                    if position.piece_type_at(25) == 1 and position.color_at(25) == 1:
                        self.blockagesBLACK -= BISHOP_TRAPPED_A6
                case 23:
                    if position.piece_type_at(30) == 1 and position.color_at(30) == 1:
                        self.blockagesBLACK -= BISHOP_TRAPPED_A6
                case 61:
                    if position.piece_type_at(62) == 6 and position.color_at(62) == 0:
                        self.positionalThemesBLACK += RETURNING_BISHOP
                case 58:
                    if position.piece_type_at(57) == 6 and position.color_at(57) == 0:
                        self.positionalThemesBLACK += RETURNING_BISHOP

        #mobility and king attacks
        dirs = [9, -7, -9, 7]
        mob = 0
        att = 0
        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[0]
            if sqNew % 8 > sqRest and sqNew < 64:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break
        # each direction
        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[1]
            if sqNew % 8 > sqRest and sqNew >= 0:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[2]
            if sqNew % 8 < sqRest and sqNew >= 0:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[3]
            if sqNew % 8 < sqRest and sqNew < 64:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        if color:
            self.mgMobWHITE += 3*(mob-7)
            self.egMobWHITE += 3 * (mob - 7)
            if att:
                self.attCountWHITE += 1
                self.attWeightWHITE += 2 * att
        else:
            self.mgMobBLACK += 3 * (mob - 7)
            self.egMobBLACK += 3 * (mob - 7)
            if att:
                self.attCountBLACK += 1
                self.attWeightBLACK += 2 * att



    def evalRook(self, sq, color, position):
        ownBlockingPawns = 0
        oppBlockingPawns = 0

        if color:
            self.materialAdjWHITE += self.rook_adj[self.Wcount[0]]
            nextSq = sq + 8
            while nextSq <= 63:
                if position.piece_type_at(nextSq) == 1:
                    if position.color_at(nextSq) == color:
                        ownBlockingPawns += 1
                        break
                    else:
                        oppBlockingPawns += 1
                nextSq += 8
            if not ownBlockingPawns:
                if not oppBlockingPawns:
                    self.mgMobWHITE += ROOK_OPEN
                    self.egMobWHITE += ROOK_OPEN
                else:
                    self.mgMobWHITE += ROOK_HALF_OPEN
                    self.egMobWHITE += ROOK_HALF_OPEN
        else:
            self.materialAdjBLACK += self.rook_adj[self.Bcount[0]]
            nextSq = sq - 8
            while nextSq >= 0:
                if position.piece_type_at(nextSq) == 1:
                    if position.color_at(nextSq) == color:
                        ownBlockingPawns += 1
                        break
                    else:
                        oppBlockingPawns += 1
                nextSq -= 8
            if not ownBlockingPawns:
                if not oppBlockingPawns:
                    self.mgMobBLACK += ROOK_OPEN
                    self.egMobBLACK += ROOK_OPEN
                else:
                    self.mgMobBLACK += ROOK_HALF_OPEN
                    self.egMobBLACK += ROOK_HALF_OPEN

        # mobility and king attacks
        dirs = [1, -8, -1, 8]
        mob = 0
        att = 0
        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[0]
            if sqNew % 8 > sqRest:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqNew = sqNew + dirs[1]
            if sqNew >= 0:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[2]
            if sqNew % 8 < sqRest:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqNew = sqNew + dirs[3]
            if sqNew < 64:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        if color:
            self.mgMobWHITE += 2*(mob-7)
            self.egMobWHITE += 4 * (mob - 7)
            if att:
                self.attCountWHITE += 1
                self.attWeightWHITE += 3 * att
        else:
            self.mgMobBLACK += 2 * (mob - 7)
            self.egMobBLACK += 4 * (mob - 7)
            if att:
                self.attCountBLACK += 1
                self.attWeightBLACK += 3 * att


    def evalQueen(self, sq, color, position):

        if color and sq > 15:
            if position.piece_type_at(1) == 2 and position.color_at(1) == 1:
                self.positionalThemesWHITE -= 2
            if position.piece_type_at(2) == 3 and position.color_at(2) == 1:
                self.positionalThemesWHITE -= 2
            if position.piece_type_at(5) == 3 and position.color_at(5) == 1:
                self.positionalThemesWHITE -= 2
            if position.piece_type_at(6) == 2 and position.color_at(6) == 1:
                self.positionalThemesWHITE -= 2
        if not color and sq < 48:
            if position.piece_type_at(57) == 2 and position.color_at(57) == 0:
                self.positionalThemesBLACK -= 2
            if position.piece_type_at(58) == 3 and position.color_at(58) == 0:
                self.positionalThemesBLACK -= 2
            if position.piece_type_at(61) == 3 and position.color_at(61) == 0:
                self.positionalThemesBLACK -= 2
            if position.piece_type_at(62) == 2 and position.color_at(62) == 0:
                self.positionalThemesBLACK -= 2

        # mobility and king attacks
        dirs = [8, 9, 1, -7, -8, -9, -1, 7]
        mob = 0
        att = 0
        sqNew = sq
        for i in range(8):
            sqNew = sqNew + dirs[0]
            if sqNew < 64:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[1]
            if sqNew % 8 > sqRest and sqNew < 64:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[2]
            if sqNew % 8 > sqRest:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[3]
            if sqNew % 8 > sqRest and sqNew >= 0:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqNew = sqNew + dirs[4]
            if sqNew >= 0:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[5]
            if sqNew % 8 < sqRest and sqNew >= 0:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[6]
            if sqNew % 8 < sqRest:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        sqNew = sq
        for i in range(8):
            sqRest = sqNew % 8
            sqNew = sqNew + dirs[7]
            if sqNew % 8 < sqRest and sqNew < 64:
                if not position.piece_at(sqNew):
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                elif position.color_at(sqNew) != color:
                    mob += 1
                    if self.sqNearK(not color, sqNew):
                        att += 1
                    break
                else:
                    break
            else:
                break

        if color:
            self.mgMobWHITE += 1*(mob-14)
            self.egMobWHITE += 2 * (mob - 14)
            if att:
                self.attCountWHITE += 1
                self.attWeightWHITE += 4 * att
        else:
            self.mgMobBLACK += 1 * (mob - 14)
            self.egMobBLACK += 2 * (mob - 14)
            if att:
                self.attCountBLACK += 1
                self.attWeightBLACK += 4 * att

    def sqNearK(self, color, sq):
        if color:
            if sq in self.sqNearKingWhite:
                return True
            return False
        else:
            if sq in self.sqNearKingBlack:
                return True
            return False


    def kingShield(self, position, sqKingWhite, sqKingBlack):
        resultWhite, resultBlack = 0, 0
        dirs1, dirs2 = [8, 9, 1, -7], [-8, -9, -1, 7]
        self.sqNearKingWhite.append(sqKingWhite)
        self.sqNearKingBlack.append(sqKingBlack)

        for i in dirs1:
            sqRold = sqKingWhite % 8
            sqNew = sqKingWhite + i
            if sqNew % 8 >= sqRold and sqNew >= 0 and sqNew < 64:
                self.sqNearKingWhite.append(sqNew)
            sqRold = sqKingBlack % 8
            sqNew = sqKingBlack + i
            if sqNew % 8 >= sqRold and sqNew >= 0 and sqNew < 64:
                self.sqNearKingBlack.append(sqNew)
        for i in dirs2:
            sqRold = sqKingWhite % 8
            sqNew = sqKingWhite + i
            if sqNew % 8 <= sqRold and sqNew >= 0 and sqNew < 64:
                self.sqNearKingWhite.append(sqNew)
            sqRold = sqKingBlack % 8
            sqNew = sqKingBlack + i
            if sqNew % 8 <= sqRold and sqNew >= 0 and sqNew < 64:
                self.sqNearKingBlack.append(sqNew)

        if sqKingWhite in [5,6,7]:
            if position.piece_type_at(13) == 1 and position.color_at(13) == 1:
                resultWhite += SHIELD1
            elif position.piece_type_at(21) == 1 and position.color_at(21) == 1:
                resultWhite += SHIELD2
            if position.piece_type_at(14) == 1 and position.color_at(14) == 1:
                resultWhite += SHIELD1
            elif position.piece_type_at(22) == 1 and position.color_at(22) == 1:
                resultWhite += SHIELD2
            if position.piece_type_at(15) == 1 and position.color_at(15) == 1:
                resultWhite += SHIELD1
            elif position.piece_type_at(23) == 1 and position.color_at(23) == 1:
                resultWhite += SHIELD2
        elif sqKingWhite in [0,1,2]:
            if position.piece_type_at(8) == 1 and position.color_at(8) == 1:
                resultWhite += SHIELD1
            elif position.piece_type_at(16) == 1 and position.color_at(16) == 1:
                resultWhite += SHIELD2
            if position.piece_type_at(9) == 1 and position.color_at(9) == 1:
                resultWhite += SHIELD1
            elif position.piece_type_at(17) == 1 and position.color_at(17) == 1:
                resultWhite += SHIELD2
            if position.piece_type_at(10) == 1 and position.color_at(10) == 1:
                resultWhite += SHIELD1
            elif position.piece_type_at(18) == 1 and position.color_at(18) == 1:
                resultWhite += SHIELD2

        if sqKingBlack in [61, 62, 63]:
            if position.piece_type_at(53) == 1 and position.color_at(53) == 0:
                resultBlack += SHIELD1
            elif position.piece_type_at(45) == 1 and position.color_at(45) == 0:
                resultBlack += SHIELD2
            if position.piece_type_at(54) == 1 and position.color_at(54) == 0:
                resultBlack += SHIELD1
            elif position.piece_type_at(46) == 1 and position.color_at(46) == 0:
                resultBlack += SHIELD2
            if position.piece_type_at(55) == 1 and position.color_at(55) == 0:
                resultBlack += SHIELD1
            elif position.piece_type_at(47) == 1 and position.color_at(47) == 0:
                resultBlack += SHIELD2
        elif sqKingBlack in [56, 57, 58]:
            if position.piece_type_at(48) == 1 and position.color_at(48) == 0:
                resultBlack += SHIELD1
            elif position.piece_type_at(40) == 1 and position.color_at(40) == 0:
                resultBlack += SHIELD2
            if position.piece_type_at(49) == 1 and position.color_at(49) == 0:
                resultBlack += SHIELD1
            elif position.piece_type_at(41) == 1 and position.color_at(41) == 0:
                resultBlack += SHIELD2
            if position.piece_type_at(50) == 1 and position.color_at(50) == 0:
                resultBlack += SHIELD1
            elif position.piece_type_at(42) == 1 and position.color_at(42) == 0:
                resultBlack += SHIELD2

        return resultWhite, resultBlack

    def evalPawnStructure(self, squareSetWhite, squareSetBlack, position):
        key = pawn_only_hash(position)
        entry = self.positionTable.checkKillerMove(key, 0, 0)
        if entry:
            return entry

        result = 0
        for pc in squareSetWhite:
            result += self.evalPawn(pc, 1, position)
        for pc in squareSetBlack:
            result -= self.evalPawn(pc, 0, position)

        self.positionTable.storeKillerMove(key, result, 0, 0)

        return result

    def evalPawn(self, sq, color, position):
        result = 0
        flagIsPassed = 1
        flagIsDirectlySupported = 0
        flagIsWeak = 1
        flagIsOpposed = 0
        leftEdge = False
        rightEdge = False

        if color:
            nextSq = sq + 8
            sqRold = sq % 8
            if (nextSq - 1) % 8 > sqRold:
                leftEdge = True
            if (nextSq + 1) % 8 < sqRold:
                rightEdge = True
            while nextSq < 64:
                if position.piece_type_at(nextSq) == 1:
                    flagIsPassed = 0
                    if position.color_at(nextSq) == color:
                        result -= DOUBLED_PAWN
                    else:
                        flagIsOpposed = 1
                if not leftEdge and position.piece_type_at(nextSq - 1) == 1 and position.color_at(nextSq - 1) != color:
                    flagIsPassed = 0
                if not rightEdge and position.piece_type_at(nextSq + 1) == 1 and position.color_at(nextSq + 1) != color:
                    flagIsPassed = 0
                nextSq += 8

            nextSq = sq
            count = 0
            while nextSq >= 0:
                if not leftEdge and position.piece_type_at(nextSq - 1) == 1 and position.color_at(nextSq - 1) == color:
                    flagIsWeak = 0
                    if count <= 1:
                        flagIsDirectlySupported = 1
                    break
                if not rightEdge and position.piece_type_at(nextSq + 1) == 1 and position.color_at(nextSq + 1) == color:
                    flagIsWeak = 0
                    if count <= 1:
                        flagIsDirectlySupported = 1
                    break
                nextSq -= 8
                count += 1

            if flagIsPassed:
                row = int(math.floor(sq/8))
                if flagIsDirectlySupported:
                    result += PROTECTED_PASSER[row]
                else:
                    result += PASSED_PAWN[row]

        else:
            nextSq = sq - 8
            sqRold = sq % 8
            if (nextSq - 1) % 8 > sqRold:
                leftEdge = True
            if (nextSq + 1) % 8 < sqRold:
                rightEdge = True
            while nextSq >= 0:
                if position.piece_type_at(nextSq) == 1:
                    flagIsPassed = 0
                    if position.color_at(nextSq) == color:
                        result -= DOUBLED_PAWN
                    else:
                        flagIsOpposed = 1
                if not leftEdge and position.piece_type_at(nextSq - 1) == 1 and position.color_at(nextSq - 1) != color:
                    flagIsPassed = 0
                if not rightEdge and position.piece_type_at(nextSq + 1) == 1 and position.color_at(nextSq + 1) != color:
                    flagIsPassed = 0
                nextSq -= 8

            nextSq = sq
            count = 0
            while nextSq < 64:
                if not leftEdge and position.piece_type_at(nextSq - 1) == 1 and position.color_at(nextSq - 1) == color:
                    flagIsWeak = 0
                    if count <= 1:
                        flagIsDirectlySupported = 1
                    break
                if not rightEdge and position.piece_type_at(nextSq + 1) == 1 and position.color_at(nextSq + 1) == color:
                    flagIsWeak = 0
                    if count <= 1:
                        flagIsDirectlySupported = 1
                    break
                nextSq += 8
                count += 1

            if flagIsPassed:
                row = int(math.floor(sq/8))
                if flagIsDirectlySupported:
                    result += PROTECTED_PASSER[7 - row]
                else:
                    result += PASSED_PAWN[7 - row]

        if flagIsWeak:
            result -= WEAK_PAWN[sqRold]
            if not flagIsOpposed:
                result -= 4

        return result

    def blockedPieces(self, position):
        # central pawn blocked -> bishop hard to develop
        if (position.piece_type_at(2) == 3 and position.color_at(2) == 1) and (position.piece_type_at(11) == 1 and position.color_at(11) == 1) and position.piece_at(19):
            self.blockagesWHITE -= BLOCK_CENTRAL_PAWN
        if (position.piece_type_at(5) == 3 and position.color_at(5) == 1) and (position.piece_type_at(12) == 1 and position.color_at(12) == 1) and position.piece_at(20):
            self.blockagesWHITE -= BLOCK_CENTRAL_PAWN
        if (position.piece_type_at(58) == 3 and position.color_at(58) == 0) and (position.piece_type_at(51) == 1 and position.color_at(51) == 0) and position.piece_at(43):
            self.blockagesBLACK -= BLOCK_CENTRAL_PAWN
        if (position.piece_type_at(61) == 3 and position.color_at(61) == 0) and (position.piece_type_at(52) == 1 and position.color_at(52) == 0) and position.piece_at(44):
            self.blockagesBLACK -= BLOCK_CENTRAL_PAWN

        #uncastled king blocking rook
        if ((position.piece_type_at(5) == 6 and position.color_at(5) == 1) or (position.piece_type_at(6) == 6 and position.color_at(6) == 1)) and ((position.piece_type_at(7) == 4 and position.color_at(7) == 1) or (position.piece_type_at(6) == 4 and position.color_at(6) == 1)):
            self.blockagesWHITE -= KING_BLOCKS_ROOK
        elif ((position.piece_type_at(1) == 6 and position.color_at(1) == 1) or (position.piece_type_at(2) == 6 and position.color_at(2) == 1)) and ((position.piece_type_at(0) == 4 and position.color_at(0) == 1) or (position.piece_type_at(1) == 4 and position.color_at(1) == 1)):
            self.blockagesWHITE -= KING_BLOCKS_ROOK
        if ((position.piece_type_at(61) == 6 and position.color_at(61) == 0) or (position.piece_type_at(62) == 6 and position.color_at(62) == 0)) and ((position.piece_type_at(62) == 4 and position.color_at(62) == 0) or (position.piece_type_at(63) == 4 and position.color_at(63) == 0)):
            self.blockagesBLACK -= KING_BLOCKS_ROOK
        elif ((position.piece_type_at(57) == 6 and position.color_at(57) == 0) or (position.piece_type_at(58) == 6 and position.color_at(58) == 0)) and ((position.piece_type_at(56) == 4 and position.color_at(56) == 0) or (position.piece_type_at(57) == 4 and position.color_at(57) == 0)):
            self.blockagesBLACK -= KING_BLOCKS_ROOK


    def phase(self, position):
        phase = 24
        for sq in range(64):
            pc = position.piece_at(sq)
            if pc:
                typ = pc.piece_type - 1
                phase -= self.phaseWeight[typ]
        if phase < 0:
            phase = 0
        phase = (phase*256)/24
        return phase







class Evaluator2:
    def __init__(self):
        midGameValueTable = [82, 337, 365, 477, 1025,  20000]
        endGameValueTable = [94, 281, 297, 512,  936,  20000]
        self.phaseWeight = [0, 1, 1, 2, 4, 0]

        mgPawns = [0,   0,   0,   0,   0,   0,  0,   0,
     98, 134,  61,  95,  68, 126, 34, -11,
     -6,   7,  26,  31,  65,  56, 25, -20,
    -14,  13,   6,  21,  23,  12, 17, -23,
    -27,  -2,  -5,  12,  17,   6, 10, -25,
    -26,  -4,  -4, -10,   3,   3, 33, -12,
    -35,  -1, -20, -23, -15,  24, 38, -22,
      0,   0,   0,   0,   0,   0,  0,   0]
        egPawns = [ 0,   0,   0,   0,   0,   0,   0,   0,
    178, 173, 158, 134, 147, 132, 165, 187,
     94, 100,  85,  67,  56,  53,  82,  84,
     32,  24,  13,   5,  -2,   4,  17,  17,
     13,   9,  -3,  -7,  -7,  -8,   3,  -1,
      4,   7,  -6,   1,   0,  -5,  -1,  -8,
     13,   8,   8,  10,  13,   0,   2,  -7,
      0,   0,   0,   0,   0,   0,   0,   0]
        mgKnights = [-167, -89, -34, -49,  61, -97, -15, -107,
     -73, -41,  72,  36,  23,  62,   7,  -17,
     -47,  60,  37,  65,  84, 129,  73,   44,
      -9,  17,  19,  53,  37,  69,  18,   22,
     -13,   4,  16,  13,  28,  19,  21,   -8,
     -23,  -9,  12,  10,  19,  17,  25,  -16,
     -29, -53, -12,  -3,  -1,  18, -14,  -19,
    -105, -21, -58, -33, -17, -28, -19,  -23]
        egKnights = [-58, -38, -13, -28, -31, -27, -63, -99,
    -25,  -8, -25,  -2,  -9, -25, -24, -52,
    -24, -20,  10,   9,  -1,  -9, -19, -41,
    -17,   3,  22,  22,  22,  11,   8, -18,
    -18,  -6,  16,  25,  16,  17,   4, -18,
    -23,  -3,  -1,  15,  10,  -3, -20, -22,
    -42, -20, -10,  -5,  -2, -20, -23, -44,
    -29, -51, -23, -15, -22, -18, -50, -64]
        mgBishops = [-29,   4, -82, -37, -25, -42,   7,  -8,
    -26,  16, -18, -13,  30,  59,  18, -47,
    -16,  37,  43,  40,  35,  50,  37,  -2,
     -4,   5,  19,  50,  37,  37,   7,  -2,
     -6,  13,  13,  26,  34,  12,  10,   4,
      0,  15,  15,  15,  14,  27,  18,  10,
      4,  15,  16,   0,   7,  21,  33,   1,
    -33,  -3, -14, -21, -13, -12, -39, -21]
        egBishops = [-14, -21, -11,  -8, -7,  -9, -17, -24,
     -8,  -4,   7, -12, -3, -13,  -4, -14,
      2,  -8,   0,  -1, -2,   6,   0,   4,
     -3,   9,  12,   9, 14,  10,   3,   2,
     -6,   3,  13,  19,  7,  10,  -3,  -9,
    -12,  -3,   8,  10, 13,   3,  -7, -15,
    -14, -18,  -7,  -1,  4,  -9, -15, -27,
    -23,  -9, -23,  -5, -9, -16,  -5, -17]
        mgRooks = [32,  42,  32,  51, 63,  9,  31,  43,
     27,  32,  58,  62, 80, 67,  26,  44,
     -5,  19,  26,  36, 17, 45,  61,  16,
    -24, -11,   7,  26, 24, 35,  -8, -20,
    -36, -26, -12,  -1,  9, -7,   6, -23,
    -45, -25, -16, -17,  3,  0,  -5, -33,
    -44, -16, -20,  -9, -1, 11,  -6, -71,
    -19, -13,   1,  17, 16,  7, -37, -26]
        egRooks = [13, 10, 18, 15, 12,  12,   8,   5,
    11, 13, 13, 11, -3,   3,   8,   3,
     7,  7,  7,  5,  4,  -3,  -5,  -3,
     4,  3, 13,  1,  2,   1,  -1,   2,
     3,  5,  8,  4, -5,  -6,  -8, -11,
    -4,  0, -5, -1, -7, -12,  -8, -16,
    -6, -6,  0,  2, -9,  -9, -11,  -3,
    -9,  2,  3, -1, -5, -13,   4, -20]
        mgQueens = [-28,   0,  29,  12,  59,  44,  43,  45,
    -24, -39,  -5,   1, -16,  57,  28,  54,
    -13, -17,   7,   8,  29,  56,  47,  57,
    -27, -27, -16, -16,  -1,  17,  -2,   1,
     -9, -26,  -9, -10,  -2,  -4,   3,  -3,
    -14,   2, -11,  -2,  -5,   2,  14,   5,
    -35,  -8,  11,   2,   8,  15,  -3,   1,
     -1, -18,  -9,  10, -15, -25, -31, -50]
        egQueens = [-9,  22,  22,  27,  27,  19,  10,  20,
    -17,  20,  32,  41,  58,  25,  30,   0,
    -20,   6,   9,  49,  47,  35,  19,   9,
      3,  22,  24,  45,  57,  40,  57,  36,
    -18,  28,  19,  47,  31,  34,  39,  23,
    -16, -27,  15,   6,   9,  17,  10,   5,
    -22, -23, -30, -16, -16, -23, -36, -32,
    -33, -28, -22, -43,  -5, -32, -20, -41]
        mgKing = [-65,  23,  16, -15, -56, -34,   2,  13,
     29,  -1, -20,  -7,  -8,  -4, -38, -29,
     -9,  24,   2, -16, -20,   6,  22, -22,
    -17, -20, -12, -27, -30, -25, -14, -36,
    -49,  -1, -27, -39, -46, -44, -33, -51,
    -14, -14, -22, -46, -44, -30, -15, -27,
      1,   7,  -8, -64, -43, -16,   9,   8,
    -15,  36,  12, -54,   8, -28,  24,  14]
        egKing = [-74, -35, -18, -18, -11,  15,   4, -17,
    -12,  17,  14,  17,  17,  38,  23,  11,
     10,  17,  23,  15,  20,  45,  44,  13,
     -8,  22,  24,  27,  26,  33,  26,   3,
    -18,  -4,  21,  24,  27,  23,   9, -11,
    -19,  -3,  11,  21,  23,  16,   7,  -9,
    -27, -11,   4,  13,  14,   4,  -5, -17,
    -53, -34, -21, -11, -28, -14, -24, -43]

        self.mgTable = [mgPawns, mgKnights, mgBishops, mgRooks, mgQueens, mgKing]
        self.egTable = [egPawns, egKnights, egBishops, egRooks, egQueens, egKing]
        for i in range(6):
            for j in range(64):
                self.mgTable[i][j] += midGameValueTable[i]
                self.egTable[i][j] += endGameValueTable[i]

        self.sqw = []
        self.sqb = []
        for i in range(64):
            sqbr = math.floor(i/8)
            self.sqw.append((7-sqbr) * 8 + (i % 8))
            self.sqb.append(sqbr * 8 + 7 - (i % 8))


    def lazyEval(self, position, who2Move, alpha, beta):
        midGameScore = 0
        endGameScore = 0
        Wcount = [0, 0, 0, 0, 0, 0]
        Bcount = [0, 0, 0, 0, 0, 0]
        phase = 24

        for typ in range(5):
            square_set = position.pieces(typ+1, 1)
            Wcount[typ] = len(square_set)
            for sq in square_set:
                sqw = self.sqw[sq]
                midGameScore += self.mgTable[typ][sqw]
                endGameScore += self.egTable[typ][sqw]
                phase -= self.phaseWeight[typ]

            square_set = position.pieces(typ + 1, 0)
            Bcount[typ] = len(square_set)
            for sq in square_set:
                sqb = self.sqb[sq]
                midGameScore -= self.mgTable[typ][sqb]
                endGameScore -= self.egTable[typ][sqb]
                phase -= self.phaseWeight[typ]

        kingW = self.sqw[position.king(1)]
        midGameScore += self.mgTable[5][kingW]
        endGameScore += self.egTable[5][kingW]

        kingB = self.sqb[position.king(0)]
        midGameScore -= self.mgTable[5][kingB]
        endGameScore -= self.egTable[5][kingB]

        """
        pcs = position.piece_map()
        for entry in pcs.items():
            typ = entry[1].piece_type - 1
            sq = entry[0]
            if entry[1].color:
                Wcount[typ] += 1
                sqbr = math.floor(sq / 8)
                sqb = (7-sqbr) * 8 + (sq % 8)
                midGameScore += self.mgTable[typ][sqb]
                endGameScore += self.egTable[typ][sqb]
                phase -= self.phaseWeight[typ]
            else:
                Bcount[typ] += 1
                midGameScore -= self.mgTable[typ][sq]
                endGameScore -= self.egTable[typ][sq]
                phase -= self.phaseWeight[typ]
        
        
        for sq in range(64):
            pc = position.piece_at(sq)
            if pc:
                typ = pc.piece_type - 1
                if pc.color:
                    Wcount[typ] += 1
                    sqw = self.sqw[sq]
                    midGameScore += self.mgTable[typ][sqw]
                    endGameScore += self.egTable[typ][sqw]
                    phase -= self.phaseWeight[typ]
                else:
                    Bcount[typ] += 1
                    sqb = self.sqb[sq]
                    midGameScore -= self.mgTable[typ][sqb]
                    endGameScore -= self.egTable[typ][sqb]
                    phase -= self.phaseWeight[typ]
        """
        if phase < 0:
            phase = 0
        phase = (phase*256)/24
        evaluation = ((((midGameScore*(256-phase)) + (endGameScore*phase)) / 256) * who2Move)

        if evaluation > beta*1.3 or evaluation < alpha*0.7:
            return evaluation

        return self.evaluate(Wcount, Bcount, midGameScore, endGameScore, position, phase, who2Move)

    def evaluate(self, Wcount, Bcount, midGameScore, endGameScore, position, phase, who2Move):

        if (Wcount[2] == 2) != (Bcount[2] == 2):
            mult = getTrue(Wcount[2] == 2)
            midGameScore += (20 * mult)
            endGameScore += (30 * mult)

        if (Wcount[1] == 2) != (Bcount[1] == 2):
            mult = getTrue(Wcount[1] == 2)
            midGameScore -= (10 * mult)
            endGameScore -= (10 * mult)

        if (Wcount[3] == 2) != (Bcount[3] == 2):
            mult = getTrue(Wcount[3] == 2)
            midGameScore -= (10 * mult)
            endGameScore -= (10 * mult)

        if (Wcount[0] == 0) != (Bcount[0] == 0):
            mult = getTrue(Wcount[0] == 0)
            midGameScore -= (20 * mult)
            endGameScore -= (20 * mult)

        if position.turn:
            position.turn = not position.turn
            mobilityScoreBlack = position.pseudo_legal_moves.count()
            position.turn = not position.turn
            mobilityScoreWhite = position.pseudo_legal_moves.count()
        else:
            position.turn = not position.turn
            mobilityScoreWhite = position.pseudo_legal_moves.count()
            position.turn = not position.turn
            mobilityScoreBlack = position.pseudo_legal_moves.count()

        evaluation = ((((midGameScore*(256-phase)) + (endGameScore*phase)) / 256) * who2Move)
        if (Wcount[0] == 0) or (Bcount[0] == 0):
            if abs(evaluation) < 340:
                return 0
        return evaluation + (10*(mobilityScoreWhite-mobilityScoreBlack)*who2Move)

    def phase(self, position):
        phase = 24
        for sq in range(64):
            pc = position.piece_at(sq)
            if pc:
                typ = pc.piece_type - 1
                phase -= self.phaseWeight[typ]
        if phase < 0:
            phase = 0
        phase = (phase*256)/24
        return phase

def getTrue(bol):
    if bol:
        return 1
    else:
        return -1

def pawn_only_hash(board):
    # Create a copy of the board
    temp_board = board.copy()

    # Remove all non-pawn pieces
    for square in chess.SQUARES:
        piece = temp_board.piece_at(square)
        if piece and piece.piece_type != chess.PAWN:
            temp_board.remove_piece_at(square)

    # Reset castling rights, en passant square, and turn
    temp_board.clear_stack()  # Clears castling rights & en passant square
    temp_board.turn = chess.WHITE  # Normalize to a single side to move

    # Compute Zobrist hash
    return chess.polyglot.zobrist_hash(temp_board)