import multiprocessing
import time
import os
def test():
    time.sleep(0.2)
    for i in range(1,10):
        print("nishi"+str(i))
print(f"test is {os.getpid()}")


def you():
    time.sleep(0.5)
    for i in range(1,10):
        print("hello"+str(i))
if __name__=="__main__":
    p1=multiprocessing.Process(target=test)
    p2=multiprocessing.Process(target=you)
    p1.start()
    p2.start()
    p1.join()
    p2.join()
print(f"test is {os.getpid()}he{os.getppid()}")