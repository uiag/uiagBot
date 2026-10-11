import nnue
from evaluator import Evaluator
from transpositionTable import TranspositionTable
import time
import numpy as np
import threading
import chess.polyglot

stop_flag = threading.Event()

FUTILITY_MARGIN = [0,200,300,500]
ENDGAME_VALUE_TABLE = [94, 281, 297, 512,  936,  0]
HIDDEN = 512
STM_FEATURE = 768

class Bot:
    def __init__(self, white):
        self.white = white   
        self.count = 0
        self.evaluator = Evaluator()
        self.transpositionTable = TranspositionTable(4, 6)
    
    def load_fen(self, fen):
        rows = fen.split("/")
        last = rows[-1].split(" ")
        rows[-1] = last[0]

        board = []
        for row in rows:
            for piece in row:
                try:
                    i = int(piece)
                    while i > 0:
                        board.append(0)
                        i -= 1
                except:
                    if piece.islower():
                        board.append(piece + "b")
                    else:
                        board.append(piece + "w")
        return [board, last[1:]]

    def evaluatePosition(self, position, who2Move, state, alpha, beta):
        self.count += 1

        #if not position.legal_moves:
        outcome = position.outcome()
        if outcome:
            if outcome.winner == chess.WHITE:
                return 10000000 * who2Move
            elif outcome.winner == chess.BLACK:
                return -10000000 * who2Move
            return 0

        return self.evaluator.lazyEval(position, state.acc, who2Move, alpha, beta)

    def startSearch(self, position, depth, timeout):
        global stop_flag
        stop_flag.clear()  # Reset stop flag

        totalDur = 0
        totalEvals = 0
        totalMax = None
        totalBestMove = None

        if not depth:
            timer_thread = threading.Thread(target=start_timer, args=(timeout,))
            timer_thread.start()

            i = 0
            stopped = False
            maximum = None
            while not stop_flag.is_set() and not stopped:
                maximum, bestMove, dur, evals = self.findMove(i, position, maximum)
                if maximum:
                    try:
                        speed = evals/dur
                    except:
                        speed = "inf"
                    print(str(i) + " Move: " + str(bestMove) + " ;Evaluation: " + str(maximum) + " ;Zeit: " + str(dur) + " ;Positions searched: " + str(evals) + "; Speed: " + str(speed))
                    totalMax = maximum
                    totalBestMove = bestMove
                    totalDur += dur
                    totalEvals += evals
                    i += 1
                    if dur > 0.5*timeout:
                        stopped = True
                else:
                    stopped = True
            return totalMax, totalBestMove, totalDur, totalEvals, i

        else:
            for i in range(depth+1):
                totalMax, totalBestMove, dur, evals = self.findMove(i, position, totalMax)
                totalDur += dur
                totalEvals += evals
            return totalMax, totalBestMove, totalDur, totalEvals, depth

    #TODO: parallelize computation, add threefold repetition, widening (aspiration windows)
    def findMove(self, depth, position, estimate):
        if stop_flag.is_set():
            return None, None, None, None
        print("TT Mov (" + str(round((len(self.transpositionTable.entries)/self.transpositionTable.size)*100, 1)) + "%," + str(round((len(self.transpositionTable.killerMoves)/self.transpositionTable.size2)*100, 1)) + "%) full")
        print("TT Pos (" + str(round((len(self.evaluator.positionTable.entries) / self.evaluator.positionTable.size) * 100, 1)) + "%, " + str(round((len(self.evaluator.positionTable.killerMoves)/self.evaluator.positionTable.size2)*100, 1)) + "%) full")
        self.count = 0
        state = NNUEState(position)
        start = time.time()
        if not estimate: #Aspiration Search
            try:
                maximum, bestMove = self.PVS(depth, -10000000, 10000000, position, self.white, True, False, position.is_check(), state)
            except Exception as e:
                print(e)
                return None, None, None, None
        else:
            try:
                maximum, bestMove = self.PVS(depth, estimate-50, estimate+50, position, self.white, True, False, position.is_check(), state)
            except Exception as e:
                print(e)
                return None, None, None, None

        end = time.time()
        return maximum, bestMove, end-start, self.count

    #TODO: null windows, (futility pruning), maybe add extensions, (enhanced forward pruning), (AEL pruning), (improve late move reduction), (eval pruning)
    def PVS(self, depth, alpha, beta, position, who2Move, canNull, isPV, inCheck, state):

        if stop_flag.is_set():
            return None, None

        key = chess.polyglot.zobrist_hash(position)
        deeper, entry = self.transpositionTable.tryLookupPosition(key, depth)
        importantMove = None
        if entry:
            if deeper:
                return entry.value, entry.move
            else:
                importantMove = entry.move

        if depth == 0:
            return self.quiesce(position, alpha, beta, who2Move, state)

        #Eval Pruning / Static Null Move
        if depth < 3 and not isPV and not inCheck and abs(beta-1) > -100000:
            static_eval = self.evaluatePosition(position, who2Move, state, alpha, beta)
            eval_margin = 120*depth
            if static_eval - eval_margin >= beta:
                return static_eval - eval_margin, None

        #Null Move pruning
        if depth > 2 and canNull and not isPV and not inCheck and self.evaluatePosition(position, who2Move, state, alpha, beta) >= beta: #TODO: dont use in endgame
            position = state.turn(position)
            R = 2
            if depth > 6:
                R = 3
            val, moveDeep = self.PVS(depth-R-1, -beta, -beta+1, position, -who2Move, False, False, False, state)
            val *= -1
            position = state.turn(position)
            if val >= beta:
                return beta, None

        #Futility pruning
        futility_prune = False
        if depth <= 3 and not isPV and not inCheck and abs(alpha) < 9000 and self.evaluatePosition(position, who2Move, state, alpha, beta) + FUTILITY_MARGIN[depth] <= alpha:
            futility_prune = True

        moves = self.moveOrder(position, importantMove, key, depth)
        if not moves:
            return self.evaluatePosition(position, who2Move, state, alpha, beta), None
        bestMove = None
        bestScore = -10000
        for count, move in enumerate(moves):
            capture_promotion = False
            if position.piece_type_at(move[0].to_square) or move[0].promotion:
                capture_promotion = True
            if not futility_prune or capture_promotion:
                position = state.nnue_make_move(position, move[0])
                if position.is_check():
                    inCheck = True
                if count == 0:
                    bestScore, bestMoveDeep = self.PVS(depth-1, -beta, -alpha, position, -who2Move, canNull, True, inCheck, state)
                    position = state.nnue_unmake_move(position, move[0])
                    bestScore *= -1
                    bestMove = move[0]
                    if bestScore > alpha:
                        if bestScore >= beta:
                            #TODO: store beta cutoff
                            self.transpositionTable.storeKillerMove(key, bestScore, move, depth)
                            return bestScore, move[0]
                        alpha = bestScore
                else:
                    moveReduction = 0
                    if count > 9 and depth > 2 and not inCheck:
                        moveReduction = 2
                    elif count > 4 and depth > 2 and not inCheck:
                        moveReduction = 1
                    score, bestMoveDeep = self.PVS(depth-1-moveReduction, -alpha-1, -alpha, position, -who2Move, canNull, isPV, inCheck, state)
                    score *= -1
                    if score > alpha and score < beta:
                        score, bestMoveDeep = self.PVS(depth-1-moveReduction, -beta, -alpha, position, -who2Move, canNull, isPV, inCheck, state)
                        score *= -1
                        if score > alpha:
                            alpha = score
                    position = state.nnue_unmake_move(position, move[0])
                    if score > bestScore:
                        if score >= beta:
                            # TODO: store beta cutoff
                            self.transpositionTable.storeKillerMove(key, score, move, depth)
                            return score, move[0]
                        bestMove = move[0]
                        bestScore = score

        self.transpositionTable.storeEvaluation(key, bestScore, bestMove, depth)
        return bestScore, bestMove

    #TODO: (dont try moves with Static Exchange valuation < 0), maybe add quiesMax to stop at some point
    def quiesce(self, position, alpha, beta, who2Move, state):
        if stop_flag.is_set():
            return None, None

        key = chess.polyglot.zobrist_hash(position)
        standPat = self.evaluatePosition(position, who2Move, state, alpha, beta)
        bestValue = standPat
        if standPat >= beta:
            return standPat, None
        if alpha < standPat:
            alpha = standPat
        moves = self.moveOrder(position, None, key, 0)
        if not moves:
            return self.evaluatePosition(position, who2Move, state, alpha, beta), None
        for move in moves:
            if position.piece_type_at(move[0].to_square):
                #Delta pruning
                #if True:
                if move[0].promotion or standPat + ENDGAME_VALUE_TABLE[position.piece_type_at(move[0].to_square)-1] > alpha - 200: #TODO: no delta pruning in endgame
                    if move[0].promotion or not self.badCapture(move[0], position):
                        position = state.nnue_make_move(position, move[0])
                        score, bestMove = self.quiesce(position, -beta, -alpha, -who2Move, state)
                        score *= -1
                        position = state.nnue_unmake_move(position, move[0])
                        if score >= beta:
                            #self.transpositionTable.storeKillerMove(key, value, move, depth) #TODO: Maybe reincorporate
                            return score, None
                        if score > bestValue:
                            bestValue = score
                        if score > alpha:
                            alpha = score

            elif position.gives_check(move[0]) or move[0].promotion:
                position = state.nnue_make_move(position, move[0])
                score, bestMove = self.quiesce(position, -beta, -alpha, -who2Move, state)
                score *= -1
                position = state.nnue_unmake_move(position, move[0])
                if score >= beta:
                    # self.transpositionTable.storeKillerMove(key, value, move, depth) #TODO: Maybe reincorporate
                    return score, None
                if score > bestValue:
                    bestValue = score
                if score > alpha:
                    alpha = score

        return bestValue, None

    def badCapture(self, move, position):
        if position.piece_type_at(move.from_square) == 1:
            return 0
        if ENDGAME_VALUE_TABLE[position.piece_type_at(move.to_square)-1] >= ENDGAME_VALUE_TABLE[position.piece_type_at(move.from_square)-1] - 50:
            return 0
        if self.pawnRecapture(move.to_square, position) and ENDGAME_VALUE_TABLE[position.piece_type_at(move.to_square)-1] + 200 - ENDGAME_VALUE_TABLE[position.piece_type_at(move.from_square)-1] < 0:
            return 1
        return 0

    def pawnRecapture(self, sq, position):
        attackers = position.attackers(not position.turn, sq)
        for attacker in attackers:
            if position.piece_type_at(attacker) == 1:
                return 1
        return 0

    #TODO: improve moveOrder, especially killerMoves
    def moveOrder(self, position, importantMove, key, depth):
        moves = position.legal_moves
        if not moves:
            return None
        movesOrdered = []
        for move in moves:
            moveScore = 0
            attackers = position.attackers(position.turn, move.to_square)
            defenders = position.attackers(not position.turn, move.to_square)
            moveScore += (len(attackers) - len(defenders))
            capture = position.piece_type_at(move.to_square)
            if capture:
                if self.badCapture(move, position):
                    captureScore = 5
                else:
                    captureScore = 20 + capture
                moveScore += captureScore
            if move.promotion:
                moveScore += 10
            if self.transpositionTable.checkKillerMove(key, move, depth):
                moveScore += 500
            if position.gives_check(move):
                moveScore += 5
            movesOrdered.append([move, moveScore])
        if importantMove:
            for i in movesOrdered:
                if i[0] == importantMove:
                    i[1] = 1000
        movesOrdered.sort(key=lambda x: x[1], reverse=True)
        return movesOrdered

class NNUEState:
    __slots__ = ("acc",)
    def __init__(self, board):
        self.acc = np.zeros(HIDDEN, dtype=np.float32)
        # Build initial feature list
        feats = []
        for square, piece in board.piece_map().items():
            feats.append(feat_idx(piece, square))

        if board.turn:  # white to move
            feats.append(STM_FEATURE)

        nnue.nnue_init_accumulator(feats, self.acc)

    def _add(self, idx):
        nnue.add_accumulator(idx, self.acc)

    def _remove(self, idx):
        nnue.remove_accumulator(idx, self.acc)

    def _update_batch(self, added, removed):
        nnue.update_accumulator(added, removed, self.acc)

    def as_list(self):
        return list(self.features)

    def turn(self, board):
        board.turn = not board.turn
        if board.turn:
            self._add(768)
        else:
            self._remove(768)
        return board

    def nnue_make_move(self, board, move):
        added = []
        removed = []

        # side to move disappears
        if board.turn:
            removed.append(STM_FEATURE)

        from_sq = move.from_square
        to_sq = move.to_square
        piece = board.piece_at(from_sq)

        # moving piece
        removed.append(feat_idx(piece, from_sq))

        # captures
        if board.is_capture(move):
            if board.is_en_passant(move):
                cap_sq = to_sq - 8 if piece.color else to_sq + 8
            else:
                cap_sq = to_sq
            captured = board.piece_at(cap_sq)
            removed.append(feat_idx(captured, cap_sq))

        # castling rook
        if board.is_castling(move):
            if to_sq == chess.G1:
                rook_from, rook_to = chess.H1, chess.F1
            elif to_sq == chess.C1:
                rook_from, rook_to = chess.A1, chess.D1
            elif to_sq == chess.G8:
                rook_from, rook_to = chess.H8, chess.F8
            else:
                rook_from, rook_to = chess.A8, chess.D8

            rook = board.piece_at(rook_from)
            removed.append(feat_idx(rook, rook_from))
            added.append(feat_idx(rook, rook_to))

        # promotion / normal move
        if move.promotion:
            promoted = chess.Piece(move.promotion, piece.color)
            added.append(feat_idx(promoted, to_sq))
        else:
            added.append(feat_idx(piece, to_sq))

        # push board
        board.push(move)

        # new side to move
        if board.turn:
            added.append(STM_FEATURE)

        # apply accumulator update in ONE call
        self._update_batch(added, removed)
        return board


    def nnue_unmake_move(self, board, move):
        added = []
        removed = []

        # current side-to-move disappears
        if board.turn:
            removed.append(STM_FEATURE)

        board.pop()

        from_sq = move.from_square
        to_sq = move.to_square
        piece = board.piece_at(from_sq)

        # restore moving piece
        added.append(feat_idx(piece, from_sq))

        # remove moved/promoted piece
        if move.promotion:
            promoted = chess.Piece(move.promotion, piece.color)
            removed.append(feat_idx(promoted, to_sq))
        else:
            removed.append(feat_idx(piece, to_sq))

        # restore capture
        if board.is_capture(move):
            if board.is_en_passant(move):
                cap_sq = to_sq - 8 if piece.color else to_sq + 8
            else:
                cap_sq = to_sq
            captured = board.piece_at(cap_sq)
            added.append(feat_idx(captured, cap_sq))

        # restore rook
        if board.is_castling(move):
            if to_sq == chess.G1:
                rook_from, rook_to = chess.H1, chess.F1
            elif to_sq == chess.C1:
                rook_from, rook_to = chess.A1, chess.D1
            elif to_sq == chess.G8:
                rook_from, rook_to = chess.H8, chess.F8
            else:
                rook_from, rook_to = chess.A8, chess.D8

            rook = board.piece_at(rook_from)
            added.append(feat_idx(rook, rook_from))
            removed.append(feat_idx(rook, rook_to))

        # restored side-to-move
        if board.turn:
            added.append(STM_FEATURE)

        self._update_batch(added, removed)
        return board

def feat_idx(piece, square):
    return piece.color * 384 + (piece.piece_type - 1) * 64 + square

def start_timer(timeout):
    """Stops the function after timeout"""
    time.sleep(timeout)
    stop_flag.set()
        
        