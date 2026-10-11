import chess
import chess.polyglot
import time
import multiprocessing as mp
from bot import Bot
from evaluator import Evaluator

class ChessGame:
    def __init__(self, white):
        self.load_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        self.game = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        self.turn = self.game.turn
        self.remainingWhite = 180
        self.remainingBlack = 180
        self.bot = Bot(white)
        self.botSearch = [False, 0]
        self.increment = 2
        self.running = False
        self.managerEnemy = mp.Manager()
        self.shared_listEnemy = self.managerEnemy.list()
        self.shared_listEnemy.append([self.bot, self.game])
        self.searchOnEnemyProcess = mp.Process(target=searchOnEnemy, args=(self.shared_listEnemy,))
        self.searchOnEnemyProcess.daemon = True
        
    def load_fen(self, fen):
        rows = fen.split("/")
        last = rows[-1].split(" ")
        rows[-1] = last[0]
        self.extra = last[1:]

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
        self.board = board
        
    def reset(self):
        if self.searchOnEnemyProcess.is_alive():
            self.searchOnEnemyProcess.kill()
        self.load_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        self.game.reset()
        self.turn = self.game.turn
        self.running = False
    
    def timer(self):
        if self.turn:
            self.remainingWhite -= 0.5
            if self.remainingWhite <= 0:
                self.reset()
                print("Game won by Black on time")
        else:
            self.remainingBlack -= 0.5
            if self.remainingBlack <= 0:
                self.reset()
                print("Game won by White on time")
                
        return self.running, self.remainingWhite, self.remainingBlack
    
    def get_board(self):
        if self.game.outcome():
            print(self.game.outcome().termination)
        return self.board
    
    def move(self, feld):
        rows = "abcdefgh"
        mov = rows[int(feld[1])] + str(8-int(feld[0])) + rows[int(feld[3])] + str(8-int(feld[2]))
        if len(feld) > 4:
            mov += feld[4]
        if chess.Move.from_uci(mov) in self.game.legal_moves:
            self.game.push(chess.Move.from_uci(mov))
            if self.game.outcome():
                print(self.game.outcome())
                self.reset()
            if self.turn:
                self.remainingWhite += self.increment
            else:
                self.remainingBlack += self.increment
            self.turn = not self.turn
            self.load_fen(self.game.fen())
            print("Move: " + mov)
        elif chess.Move.from_uci(mov + "q") in self.game.legal_moves:
            return True, self.get_board()
        else:
            print("Illegal move: " + mov)
        return False, self.get_board()

    def runBotTest(self, depth, fen, color, timeForMove):

        self.bot = Bot(color)
        self.game = chess.Board(fen)

        totalMax, totalBestMove, totalDur, totalEvals, actualDepth = self.bot.startSearch(self.game, depth, timeForMove)

        return totalBestMove, totalEvals, actualDepth

        """
        with mp.Manager() as manager:
            shared_list = manager.list()
            shared_list.append([None, None, self.bot, self.game, None, None, None])
            process = mp.Process(target=searchMoveTest, args=(shared_list,))
            process.daemon = True
            process.start()  # Start worker process

            searchStopped = False
            while searchStopped == False:
                time.sleep(0.95)
                timeForMove -= 1
                moves = shared_list[-1]
                if moves[5] != depth:
                    depth = moves[5]
                    totalPos += moves[6]
                if timeForMove < 0 or depth >= maxDepth:
                    searchStopped = True
            process.terminate()
            process.join()

            moves = shared_list[-1]
            depth = moves[5]
            bestMove = moves[0]
        """
    
    def testPosition(self):
        bot = Bot(1)
        print(bot.evaluatePosition(self.game, True))

    def moveBot(self):
        search = True
        rem = 0
        if self.searchOnEnemyProcess.is_alive():
            self.searchOnEnemyProcess.terminate()
            self.searchOnEnemyProcess.join()
            self.bot = self.shared_listEnemy[-1][0]
        self.botSearch[0] = True
        if self.game.legal_moves.count() == 1:
            for move in self.game.legal_moves:
                bestMove = move
            evaluation = None
            search = False
        if self.botSearch[0] and search:
            self.botSearch[1] += 1

            phase = Evaluator().phase(self.game)
            moveCountPredict = 40 - (40/256)*phase
            if moveCountPredict < 5:
                moveCountPredict = 5
            if self.bot.white == 1:
                rem = self.remainingWhite
                if self.remainingWhite > 120:
                    timeForMove = (1.4 * self.remainingWhite / moveCountPredict) + self.increment
                    if self.botSearch[1] <= 5:
                        timeForMove *= 1.2
                    elif self.botSearch[1] <= 20:
                        timeForMove *= 1.4
                else:
                    if self.remainingWhite < 3*self.increment:
                        timeForMove = (0.75*self.remainingWhite / moveCountPredict)
                    else:
                        timeForMove = (0.95*self.remainingWhite / moveCountPredict) + self.increment
            else:
                rem = self.remainingBlack
                if self.remainingBlack > 120:
                    timeForMove = (1.4 * self.remainingBlack / moveCountPredict) + self.increment
                    if self.botSearch[1] <= 5:
                        timeForMove *= 1.2
                    elif self.botSearch[1] <= 20:
                        timeForMove *= 1.4
                else:
                    if self.remainingBlack < 3*self.increment:
                        timeForMove = (0.75*self.remainingBlack / moveCountPredict)
                    else:
                        timeForMove = (0.95*self.remainingBlack / moveCountPredict) + self.increment

            timeForMove -= 0.75
            timeForMove = max(timeForMove, 0.1)
            evaluation, bestMove, totalDur, totalEvals, actualDepth = self.bot.startSearch(self.game.copy(), None, timeForMove)

        if not bestMove:
            # if no move found, choose random legal move
            for move in self.game.legal_moves:
                bestMove = move
        self.game.push(bestMove)
        if self.game.outcome():
            print("OUTCOME")
            print(self.game.outcome())
            self.reset()
        if self.turn:
            self.remainingWhite += self.increment
        else:
            self.remainingBlack += self.increment
        self.turn = not self.turn
        self.load_fen(self.game.fen())
        print("Move by computer: " + str(bestMove) + " Evaluation: " + str(evaluation))
        if rem > 20:
            self.searchEnemy()
        return bestMove, evaluation

    def searchEnemy(self):
        self.shared_listEnemy = self.managerEnemy.list()
        self.shared_listEnemy.append([self.bot, self.game])
        self.searchOnEnemyProcess = mp.Process(target=searchOnEnemy, args=(self.shared_listEnemy,))
        self.searchOnEnemyProcess.start()  # Start worker process
        return True

    def startProcesses(self):
        self.shared_list = self.manager.list()
        self.shared_list.append([self.bot, self.game])
        self.proc1 = mp.Process(target=searchMove, args=(self.shared_list,))
        self.proc1.start()  # Start worker process
        self.proc2 = mp.Process(target=searchMove, args=(self.shared_list,))
        self.proc2.start()  # Start worker process
        self.proc3 = mp.Process(target=searchMove, args=(self.shared_list,))
        self.proc3.start()  # Start worker process
        return True
        
#TODO: if moveOrder not a lot better search all moves on enemy
def searchOnEnemy(q):
    i = 1
    maximum = None
    info = q[-1]
    bot = info[0]
    game = info[1]
    allMoves = bot.moveOrder(game, None, None, None)
    game.push(allMoves[0][0])

    maximum, move, times, count = bot.findMove(0, game, maximum)
    q[-1] = [bot, game]
    try:
        speed = count / times
    except:
        speed = "inf"
    print("On Enemy time(" + str(allMoves[0][0]) + "): " + str(0) + " Move: " + str(move) + " ;Evaluation: " + str(maximum) + " ;Zeit: " + str(times) + " ;Positions searched: " + str(count) + "; Speed: " + str(speed))

    while abs(maximum) < 9000000:
        maximum, move, times, count  = bot.findMove(i, game, maximum)
        q[-1] = [bot, game]
        try:
            speed = count/times
        except:
            speed = "inf"
        print("On Enemy time(" + str(allMoves[0][0]) + "): " + str(i) + " Move: " + str(move) + " ;Evaluation: " + str(maximum) + " ;Zeit: " + str(times) + " ;Positions searched: " + str(count) + "; Speed: " + str(speed))
        i += 1

def searchMove(q):
    i = 0
    info = q[0]
    bot = info[0]
    game = info[1]
    while True:
        maximum, move, timess, count  = bot.findMove(i, game)
        i += 1

def searchMoveTest(q):
    i = 0
    maximum = 0
    info = q[0]
    bot = info[2]
    game = info[3]
    while abs(maximum) < 9000000:
        maximum, move, timess, count  = bot.findMove(i, game)
        q[-1] = [move, maximum, bot, game, timess, i, count]
        i += 1
    
    
    