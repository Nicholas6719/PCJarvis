import sys; sys.path.insert(0, r"C:\Users\nicho\Documents\CodingProjects\Jarvis")
from jarvis.main import DISMISS
should = ["that's all","No go to sleep","no, go to sleep","go to sleep","goodbye",
          "ok that's all","just go to sleep","actually never mind","stand down",
          "jarvis, that's all","stop listening","we're done"]
should_not = ["put the computer to sleep","make my laptop go to sleep",
              "what time do I go to sleep","that's all I know about Rome",
              "stop the music","never mind the weather, what's my battery"]
bad = [t for t in should if not DISMISS.match(t)]
bad += [f"(false) {t}" for t in should_not if DISMISS.match(t)]
for t in should: print(f"{'ok  ' if DISMISS.match(t) else 'FAIL'} dismiss: {t}")
for t in should_not: print(f"{'ok  ' if not DISMISS.match(t) else 'FAIL'} keep:    {t}")
print(f"\nfailures: {len(bad)}")
