import discord  
from discord.ext import commands, tasks  
import json  
import os  
from datetime import datetime, timedelta, time  
from typing import Dict, List, Optional  
import math  
from dotenv import load\_dotenv

\# Load environment variables  
load\_dotenv()

\# Bot setup  
intents \= discord.Intents.default()  
intents.message\_content \= True  
intents.members \= True  
intents.reactions \= True  
intents.voice\_states \= True

bot \= commands.Bot(command\_prefix='$', intents=intents, help\_command=None)

\# \===== CONSTANTS \=====

INITIAL\_CASH \= 10000  
INITIAL\_PRICE \= 100  
DAILY\_REWARD \= 500  
STREAK\_BONUS \= 500  
PRICE\_IMPACT\_K \= 0.5  
INACTIVITY\_THRESHOLD \= 4 \* 3600  \# 4 hours in seconds  
INACTIVITY\_DECAY \= 0.04          \# 4% drop per inactivity event  
LIQUIDATION\_THRESHOLD \= 1.5      \# 150% of entry price  
MIN\_PRICE \= 70.00

\# Market hours (server-local offset is configurable)  
MARKET\_OPEN \= time(6, 30\)   \# 06:30  
MARKET\_CLOSE \= time(6, 25\)  \# 04:30 next day  
TIMEZONE\_OFFSET\_HOURS \= 0   \# adjust if you want local vs UTC

\# Shorts behavior  
SHORT\_FREEZE\_MINUTES \= 30

\# Activity-driven price tick  
ACTIVITY\_TICK\_MINUTES \= 15  \# 15-minute tick

\# VC ping roles (host pings)  
VC\_PING\_ROLES \= \[  
    1331261849488068628,  
    1299178128526282834,  
    1303650629821923378,  
    1302158123221651519,  
\]

\# Voice ping timing  
VOICE\_PING\_WINDOW\_SECONDS \= 5400  \# 1.5-hour window for responders  
FAST\_RESPONSE\_SECONDS \= 120       \# \<= 2 minutes \= big bonus  
MEDIUM\_RESPONSE\_SECONDS \= 300     \# \<= 5 minutes \= medium bonus

\# Extra VC 3% boost interval  
VC\_EXTRA\_BOOST\_INTERVAL\_SECONDS \= 900  \# 15 minutes

\# Photo bonus channels  
PHOTO\_BONUS\_CHANNEL\_IDS \= {  
    1295236476925378613,  
    1299105121006784707,  
}

\# Hedge fund APY / penalty  
HEDGE\_FUND\_BASE\_APY \= 0.15      \# 15% nominal monthly  
EARLY\_WITHDRAW\_PENALTY \= 0.05   \# \-5% APY per early withdrawal  
PENALTY\_DURATION\_DAYS \= 14      \# penalty lasts 2 weeks

\# Events wallet (no penalty flows)  
EVENTS\_WALLET\_USER\_ID \= "events\_wallet"  \# pseudo-user key in funds\_data

\# Data storage paths  
DATA\_DIR \= "data"  
USERS\_FILE \= os.path.join(DATA\_DIR, "users.json")  
FUNDS\_FILE \= os.path.join(DATA\_DIR, "funds.json")  
PRICES\_FILE \= os.path.join(DATA\_DIR, "prices.json")  
FUND\_PENALTIES\_FILE \= os.path.join(DATA\_DIR, "fund\_penalties.json")

\# In-memory data  
users\_data: Dict\[str, dict\] \= {}  
funds\_data: Dict\[str, dict\] \= {}  
prices\_data: Dict\[str, dict\] \= {}  
fund\_penalty\_history: Dict\[str, dict\] \= {}

\# Voice/VC tracking  
\# user\_id \-\> {"start": datetime, "channel\_id": int, "role\_ping": bool, "from\_pings": set(msg\_ids)}  
voice\_sessions: Dict\[str, dict\] \= {}  
\# message\_id \-\> {"host\_id": str, "channel\_id": int, "timestamp": str, "first\_10\_joiners": list, "extra\_joiners": list}  
voice\_ping\_sessions: Dict\[int, dict\] \= {}

\# Per-user trade cooldowns  
last\_trade\_time: Dict\[str, datetime\] \= {}  
TRADE\_COOLDOWN\_SECONDS \= 15 \* 60  \# 15 minutes, only for short/cover

\# Extra VC 3% boost tracking  
\# user\_id \-\> {"ping\_time": datetime, "last\_boost": datetime, "end\_time": datetime}  
vc\_extra\_boosts: Dict\[str, dict\] \= {}

\# Discipline penalty percentage  
DISCIPLINE\_PENALTY \= 0.17  \# 17% drop

\# \===== TIME/DATE HELPERS \=====

def get\_now\_with\_offset() \-\> datetime:  
    return datetime.utcnow() \+ timedelta(hours=TIMEZONE\_OFFSET\_HOURS)

def is\_trading\_day(dt: datetime) \-\> bool:  
    \# Monday=0 ... Sunday=6, trading Mon–Sat  
    weekday \= dt.weekday()  
    return 0 \<= weekday \<= 5

def is\_sunday(dt: datetime) \-\> bool:  
    return dt.weekday() \== 6

def is\_market\_open(dt: datetime) \-\> bool:  
    """  
    Market open 06:30 to 04:30 next day, Monday–Saturday.  
    Sunday: closed.  
    """  
    if is\_sunday(dt):  
        return False

    local\_time \= dt.time()

    if MARKET\_OPEN \< MARKET\_CLOSE:  
        return MARKET\_OPEN \<= local\_time \< MARKET\_CLOSE  
    else:  
        \# overnight window: open OR after midnight before close  
        return (local\_time \>= MARKET\_OPEN) or (local\_time \< MARKET\_CLOSE)

def is\_trade\_on\_cooldown(user\_id: str) \-\> Optional\[int\]:  
    """Return seconds remaining if on cooldown, else None."""  
    now \= get\_now\_with\_offset()  
    last \= last\_trade\_time.get(user\_id)  
    if not last:  
        return None  
    elapsed \= (now \- last).total\_seconds()  
    if elapsed \>= TRADE\_COOLDOWN\_SECONDS:  
        return None  
    return int(TRADE\_COOLDOWN\_SECONDS \- elapsed)

def set\_trade\_time(user\_id: str):  
    last\_trade\_time\[user\_id\] \= get\_now\_with\_offset()

\# \===== DATA MANAGEMENT \=====

def ensure\_data\_dir():  
    if not os.path.exists(DATA\_DIR):  
        os.makedirs(DATA\_DIR)

def load\_data():  
    global users\_data, funds\_data, prices\_data, fund\_penalty\_history

    ensure\_data\_dir()

    try:  
        if os.path.exists(USERS\_FILE):  
            with open(USERS\_FILE, 'r') as f:  
                users\_data \= json.load(f)  
        else:  
            users\_data \= {}  
    except Exception as e:  
        print(f"Error loading users data: {e}")  
        users\_data \= {}

    try:  
        if os.path.exists(FUNDS\_FILE):  
            with open(FUNDS\_FILE, 'r') as f:  
                funds\_data \= json.load(f)  
        else:  
            funds\_data \= {}  
    except Exception as e:  
        print(f"Error loading funds data: {e}")  
        funds\_data \= {}

    try:  
        if os.path.exists(PRICES\_FILE):  
            with open(PRICES\_FILE, 'r') as f:  
                prices\_data \= json.load(f)  
        else:  
            prices\_data \= {}  
    except Exception as e:  
        print(f"Error loading prices data: {e}")  
        prices\_data \= {}

    try:  
        if os.path.exists(FUND\_PENALTIES\_FILE):  
            with open(FUND\_PENALTIES\_FILE, 'r') as f:  
                fund\_penalty\_history \= json.load(f)  
        else:  
            fund\_penalty\_history \= {}  
    except Exception as e:  
        print(f"Error loading fund penalties: {e}")  
        fund\_penalty\_history \= {}

def save\_data():  
    ensure\_data\_dir()  
    try:  
        with open(USERS\_FILE, 'w') as f:  
            json.dump(users\_data, f, indent=2)  
    except Exception as e:  
        print(f"Error saving users data: {e}")  
    try:  
        with open(FUNDS\_FILE, 'w') as f:  
            json.dump(funds\_data, f, indent=2)  
    except Exception as e:  
        print(f"Error saving funds data: {e}")  
    try:  
        with open(PRICES\_FILE, 'w') as f:  
            json.dump(prices\_data, f, indent=2)  
    except Exception as e:  
        print(f"Error saving prices data: {e}")  
    try:  
        with open(FUND\_PENALTIES\_FILE, 'w') as f:  
            json.dump(fund\_penalty\_history, f, indent=2)  
    except Exception as e:  
        print(f"Error saving fund penalties: {e}")

\# \===== USER / PRICE / FUND MANAGEMENT \=====

def ensure\_user(user\_id: str):  
    if user\_id not in users\_data:  
        users\_data\[user\_id\] \= {  
            "cash\_balance": INITIAL\_CASH,  
            "net\_worth": INITIAL\_CASH,  
            "month\_start\_net\_worth": INITIAL\_CASH,  
            "portfolio": {  
                "long": {},  
                "short": {}  
            },  
            "activity": {  
                "today": {  
                    "text\_msgs": 0,  
                    "media\_msgs": 0,  
                    "voice\_minutes": 0,  
                    "voice\_unique\_channels": \[\],  
                    "reaction\_count": 0,  
                    "reply\_count": 0,  
                    "role\_ping\_joins": 0,  
                    "role\_ping\_join\_minutes": 0,  
                    "timestamp": datetime.utcnow().isoformat()  
                },  
                "week": {  
                    "text\_msgs": 0,  
                    "media\_msgs": 0,  
                    "voice\_minutes": 0,  
                    "voice\_unique\_channels": \[\],  
                    "reaction\_count": 0,  
                    "reply\_count": 0,  
                    "role\_ping\_joins": 0,  
                    "role\_ping\_join\_minutes": 0,  
                    "timestamp": datetime.utcnow().isoformat()  
                }  
            },  
            "daily": {  
                "last\_claim": None,  
                "streak": 0  
            },  
            "last\_activity": datetime.utcnow().isoformat(),  
            "opt\_in": True,  
            "intro\_shown": False  
        }  
        save\_data()

def ensure\_price(user\_id: str):  
    if user\_id not in prices\_data:  
        prices\_data\[user\_id\] \= {  
            "current": INITIAL\_PRICE,  
            "history": \[\],  
            "high\_24h": INITIAL\_PRICE,  
            "low\_24h": INITIAL\_PRICE,  
            "all\_time\_high": INITIAL\_PRICE  
        }  
        save\_data()  
    else:  
        \# Backfill all\_time\_high if missing  
        if "all\_time\_high" not in prices\_data\[user\_id\]:  
            prices\_data\[user\_id\]\["all\_time\_high"\] \= prices\_data\[user\_id\].get("current", INITIAL\_PRICE)  
            save\_data()

def update\_price\_record(user\_id: str, new\_price: float):  
    ensure\_price(user\_id)  
    price\_info \= prices\_data\[user\_id\]  
    price\_info\["current"\] \= new\_price  
    price\_info\["high\_24h"\] \= max(price\_info.get("high\_24h", new\_price), new\_price)  
    price\_info\["low\_24h"\] \= min(price\_info.get("low\_24h", new\_price), new\_price)  
    price\_info\["all\_time\_high"\] \= max(price\_info.get("all\_time\_high", new\_price), new\_price)  
    price\_info\["history"\].append({  
        "price": new\_price,  
        "timestamp": datetime.utcnow().isoformat()  
    })  
    cutoff \= datetime.utcnow() \- timedelta(hours=24)  
    price\_info\["history"\] \= \[  
        h for h in price\_info\["history"\]  
        if datetime.fromisoformat(h\["timestamp"\]) \> cutoff  
    \]

def ensure\_fund(user\_id: str):  
    if user\_id not in funds\_data:  
        funds\_data\[user\_id\] \= {  
            "name": f"Fund {user\_id}",  
            "manager\_id": user\_id,  
            "cash\_balance": 0,  
            "investors": {}  
        }  
        save\_data()

def ensure\_events\_wallet():  
    if EVENTS\_WALLET\_USER\_ID not in funds\_data:  
        funds\_data\[EVENTS\_WALLET\_USER\_ID\] \= {  
            "name": "Events Wallet",  
            "manager\_id": EVENTS\_WALLET\_USER\_ID,  
            "cash\_balance": 0,  
            "investors": {}  
        }

def calculate\_net\_worth(user\_id: str) \-\> float:  
    ensure\_user(user\_id)  
    user \= users\_data\[user\_id\]

    net\_worth \= user\["cash\_balance"\]

    for target\_id, position in user\["portfolio"\]\["long"\].items():  
        ensure\_price(target\_id)  
        current\_price \= prices\_data\[target\_id\]\["current"\]  
        net\_worth \+= position\["shares"\] \* current\_price

    for target\_id, position in user\["portfolio"\]\["short"\].items():  
        ensure\_price(target\_id)  
        current\_price \= prices\_data\[target\_id\]\["current"\]  
        entry\_value \= position\["shares"\] \* position\["entry\_price"\]  
        current\_value \= position\["shares"\] \* current\_price  
        net\_worth \+= (entry\_value \- current\_value)

    return net\_worth

\# \===== PRICE MANAGEMENT \=====

def apply\_trade\_price\_impact(target\_id: str, volume: int, is\_buy: bool):  
    ensure\_price(target\_id)  
    price \= prices\_data\[target\_id\]\["current"\]

    if is\_buy:  
        price \+= PRICE\_IMPACT\_K \* (volume / 100\)  
    else:  
        price \-= PRICE\_IMPACT\_K \* (volume / 100\)

    \# Stall and floor logic  
    if price \< MIN\_PRICE:  
        price \= MIN\_PRICE

    update\_price\_record(target\_id, price)  
    save\_data()

def get\_24h\_price\_change(user\_id: str) \-\> float:  
    ensure\_price(user\_id)  
    history \= prices\_data\[user\_id\]\["history"\]

    if not history:  
        return 0.0

    cutoff \= datetime.utcnow() \- timedelta(hours=24)  
    old\_prices \= \[h\["price"\] for h in history if datetime.fromisoformat(h\["timestamp"\]) \<= cutoff\]

    if not old\_prices:  
        old\_price \= history\[0\]\["price"\]  
    else:  
        old\_price \= old\_prices\[-1\]

    current\_price \= prices\_data\[user\_id\]\["current"\]

    if old\_price \== 0:  
        return 0.0

    return ((current\_price \- old\_price) / old\_price) \* 100

\# \===== ENGAGEMENT / TRENDING \=====

def calculate\_trending\_score(activity: dict) \-\> float:  
    text\_msgs \= activity.get("text\_msgs", 0\)  
    media\_msgs \= activity.get("media\_msgs", 0\)  
    voice\_minutes \= activity.get("voice\_minutes", 0\)  
    unique\_channels \= len(activity.get("voice\_unique\_channels", \[\]))  
    reactions \= activity.get("reaction\_count", 0\)  
    replies \= activity.get("reply\_count", 0\)  
    role\_ping\_joins \= activity.get("role\_ping\_joins", 0\)  
    role\_ping\_join\_minutes \= activity.get("role\_ping\_join\_minutes", 0\)

    def soft\_cap(x, cap):  
        return cap \* (1 \- math.exp(-x / cap))

    text\_msgs \= soft\_cap(text\_msgs, 100\)  
    media\_msgs \= soft\_cap(media\_msgs, 50\)  
    voice\_minutes \= soft\_cap(voice\_minutes, 300\)  
    reactions \= soft\_cap(reactions, 200\)  
    replies \= soft\_cap(replies, 100\)  
    role\_ping\_join\_minutes \= soft\_cap(role\_ping\_join\_minutes, 180\)

    score \= (  
        0.5 \* text\_msgs \+  
        2.0 \* media\_msgs \+  
        0.1 \* voice\_minutes \+  
        1.5 \* unique\_channels \+  
        0.2 \* reactions \+  
        0.3 \* replies \+  
        4.0 \* role\_ping\_joins \+  
        0.3 \* role\_ping\_join\_minutes  
    )

    ts\_str \= activity.get("timestamp")  
    if ts\_str:  
        try:  
            ts \= datetime.fromisoformat(ts\_str)  
            age\_hours \= (datetime.utcnow() \- ts).total\_seconds() / 3600  
            decay \= max(0.3, math.exp(-age\_hours / 72.0))  
            score \*= decay  
        except Exception:  
            pass

    return score

def get\_engagement\_tier(score: float, all\_scores: List\[float\]) \-\> str:  
    if not all\_scores:  
        return "Low"

    sorted\_scores \= sorted(all\_scores, reverse=True)  
    percentile\_rank \= (sorted\_scores.index(score) \+ 1\) / len(sorted\_scores)

    if percentile\_rank \<= 0.05:  
        return "Elite"  
    elif percentile\_rank \<= 0.30:  
        return "High"  
    elif percentile\_rank \<= 0.70:  
        return "Medium"  
    else:  
        return "Low"

def reset\_activity\_bucket(bucket: dict):  
    bucket\["text\_msgs"\] \= 0  
    bucket\["media\_msgs"\] \= 0  
    bucket\["voice\_minutes"\] \= 0  
    bucket\["voice\_unique\_channels"\] \= \[\]  
    bucket\["reaction\_count"\] \= 0  
    bucket\["reply\_count"\] \= 0  
    bucket\["role\_ping\_joins"\] \= 0  
    bucket\["role\_ping\_join\_minutes"\] \= 0  
    bucket\["timestamp"\] \= datetime.utcnow().isoformat()

def compute\_activity\_return(user\_id: str) \-\> float:  
    """  
    Map weekly engagement to a 15-minute return in %,  
    tuned for \~8h with stronger upside than downside.  
    """  
    user \= users\_data\[user\_id\]  
    week\_activity \= user\["activity"\]\["week"\]  
    score \= calculate\_trending\_score(week\_activity)

    norm \= math.log10(score \+ 1\)  
    baseline \= 0.5  
    centered \= norm \- baseline

    if centered \>= 0:  
        raw\_return \= centered \* 12.0   \# up to \~+6% per tick  
    else:  
        raw\_return \= centered \* 7.0    \# down to \~-3.5% per tick

    raw\_return \= max(-3.5, min(6.0, raw\_return))  
    return raw\_return

def apply\_floor\_stall(current\_price: float, proposed\_price: float) \-\> float:  
    """  
    Ensure price never drops below MIN\_PRICE and stalls as it approaches the floor.  
    """  
    if proposed\_price \>= current\_price:  
        \# Up move: just clamp at or above MIN\_PRICE  
        return max(proposed\_price, MIN\_PRICE)

    \# Down move  
    if current\_price \<= MIN\_PRICE:  
        return MIN\_PRICE

    distance \= max(current\_price \- MIN\_PRICE, 0.1)  \# in dollars  
    attenuation \= min(1.0, distance / 10.0)        \# closer to floor \=\> smaller downward moves  
    new\_price \= current\_price \- (current\_price \- proposed\_price) \* attenuation  
    if new\_price \< MIN\_PRICE:  
        new\_price \= MIN\_PRICE  
    return new\_price

\# \===== VC PING / RESPONDER \=====

def is\_voice\_ping\_message(message: discord.Message) \-\> bool:  
    """A message counts as a voice ping if it mentions any VC ping role."""  
    if not message.guild:  
        return False  
    if not message.role\_mentions:  
        return False  
    for role in message.role\_mentions:  
        if role.id in VC\_PING\_ROLES:  
            return True  
    return False

def reward\_voice\_ping\_response(responder\_id: str, channel\_id: int):  
    """Reward a user for joining a voice channel after a ping."""  
    ensure\_user(responder\_id)  
    now \= datetime.utcnow()  
    responder \= users\_data\[responder\_id\]

    for msg\_id, data in list(voice\_ping\_sessions.items()):  
        host\_id \= data\["host\_id"\]  
        ping\_channel\_id \= data\["channel\_id"\]  
        ts\_str \= data\["timestamp"\]  
        first\_10 \= data.get("first\_10\_joiners", \[\])  
        extra\_joiners \= data.get("extra\_joiners", \[\])

        try:  
            ping\_time \= datetime.fromisoformat(ts\_str)  
        except Exception:  
            del voice\_ping\_sessions\[msg\_id\]  
            continue

        age \= (now \- ping\_time).total\_seconds()

        \# Only joins after ping within 1.5h  
        if age \< 0 or age \> VOICE\_PING\_WINDOW\_SECONDS:  
            continue

        if ping\_channel\_id \!= channel\_id:  
            continue

        if responder\_id \== host\_id:  
            continue

        \# Ensure fields exist  
        if "first\_10\_joiners" not in data:  
            data\["first\_10\_joiners"\] \= first\_10  
        if "extra\_joiners" not in data:  
            data\["extra\_joiners"\] \= extra\_joiners

        \# 20% join boost for first 10 unique responders  
        if responder\_id not in first\_10:  
            if len(first\_10) \< 10:  
                ensure\_price(responder\_id)  
                current\_price \= prices\_data\[responder\_id\]\["current"\]  
                boosted \= current\_price \* 1.20  
                boosted \= apply\_floor\_stall(current\_price, boosted)  
                update\_price\_record(responder\_id, boosted)  
                data\["first\_10\_joiners"\].append(responder\_id)

                \# Track that this join came from this ping for 1h stay bonus  
                if responder\_id in voice\_sessions:  
                    voice\_sessions\[responder\_id\].setdefault("from\_pings", set()).add(msg\_id)  
            else:  
                \# Extra responders beyond first 10 get tracked for 3% periodic boosts  
                if responder\_id not in extra\_joiners:  
                    data\["extra\_joiners"\].append(responder\_id)  
                    vc\_extra\_boosts.setdefault(responder\_id, {  
                        "ping\_time": ping\_time,  
                        "last\_boost": now,  
                        "end\_time": ping\_time \+ timedelta(seconds=VOICE\_PING\_WINDOW\_SECONDS)  
                    })  
        else:  
            \# Already in first\_10, just ensure their ping mapping exists  
            if responder\_id in voice\_sessions:  
                voice\_sessions\[responder\_id\].setdefault("from\_pings", set()).add(msg\_id)

        \# Engagement points based on speed (unchanged)  
        if age \<= FAST\_RESPONSE\_SECONDS:  
            speed\_mult \= 3.0  
        elif age \<= MEDIUM\_RESPONSE\_SECONDS:  
            speed\_mult \= 2.0  
        else:  
            speed\_mult \= 1.0

        base\_points \= 5.0  
        bonus \= base\_points \* speed\_mult

        responder\["activity"\]\["today"\]\["role\_ping\_join\_minutes"\] \+= bonus  
        responder\["activity"\]\["week"\]\["role\_ping\_join\_minutes"\] \+= bonus

        host \= users\_data.get(host\_id)  
        if host:  
            host\["activity"\]\["today"\]\["role\_ping\_joins"\] \+= 0.5  
            host\["activity"\]\["week"\]\["role\_ping\_joins"\] \+= 0.5

\# \===== HEDGE FUND PENALTY & EVENTS \=====

def get\_user\_penalty\_apr(user\_id: str) \-\> float:  
    """Return the temporary APY penalty for this user, if any."""  
    rec \= fund\_penalty\_history.get(user\_id)  
    if not rec:  
        return 0.0  
    until\_str \= rec.get("penalty\_until")  
    if not until\_str:  
        return 0.0  
    try:  
        until \= datetime.fromisoformat(until\_str)  
    except Exception:  
        return 0.0  
    now \= datetime.utcnow()  
    if now \>= until:  
        return 0.0  
    return rec.get("penalty\_apr", 0.0)

def apply\_early\_withdraw\_penalty(user\_id: str):  
    """Apply or extend a penalty if the user withdraws early."""  
    now \= datetime.utcnow()  
    rec \= fund\_penalty\_history.get(user\_id, {  
        "penalty\_apr": 0.0,  
        "penalty\_until": now.isoformat()  
    })  
    new\_penalty \= rec\["penalty\_apr"\] \+ EARLY\_WITHDRAW\_PENALTY  
    penalty\_until \= now \+ timedelta(days=PENALTY\_DURATION\_DAYS)  
    fund\_penalty\_history\[user\_id\] \= {  
        "penalty\_apr": new\_penalty,  
        "penalty\_until": penalty\_until.isoformat()  
    }  
    save\_data()

\# \===== BOT EVENTS \=====

@bot.event  
async def on\_ready():  
    print(f'{bot.user} is online\!')  
    load\_data()  
    ensure\_events\_wallet()  
    monthly\_rollover\_check.start()  
    inactivity\_price\_decay.start()  
    short\_liquidation\_check.start()  
    short\_freeze\_check.start()  
    activity\_price\_step.start()  
    vc\_extra\_boost\_step.start()

@bot.event  
async def on\_message(message):  
    if message.author.bot:  
        return

    user\_id \= str(message.author.id)  
    ensure\_user(user\_id)  
    ensure\_price(user\_id)

    user \= users\_data\[user\_id\]  
    user\["last\_activity"\] \= datetime.utcnow().isoformat()

    \# Photo bonus channels  
    if message.attachments:  
        user\["activity"\]\["today"\]\["media\_msgs"\] \+= 1  
        user\["activity"\]\["week"\]\["media\_msgs"\] \+= 1

        if message.channel and message.channel.id in PHOTO\_BONUS\_CHANNEL\_IDS:  
            \# Big bonus for media in special channels  
            user\["activity"\]\["today"\]\["role\_ping\_join\_minutes"\] \+= 10.0  
            user\["activity"\]\["week"\]\["role\_ping\_join\_minutes"\] \+= 10.0  
    else:  
        user\["activity"\]\["today"\]\["text\_msgs"\] \+= 1  
        user\["activity"\]\["week"\]\["text\_msgs"\] \+= 1

    if message.reference and getattr(message.reference, 'message\_id', None):  
        user\["activity"\]\["today"\]\["reply\_count"\] \+= 1  
        user\["activity"\]\["week"\]\["reply\_count"\] \+= 1

    \# Voice ping detection  
    member \= message.author  
    if isinstance(member, discord.Member) and member.voice and member.voice.channel:  
        if is\_voice\_ping\_message(message):  
            voice\_ping\_sessions\[message.id\] \= {  
                "host\_id": user\_id,  
                "channel\_id": member.voice.channel.id,  
                "timestamp": datetime.utcnow().isoformat(),  
                "first\_10\_joiners": \[\],  
                "extra\_joiners": \[\]  
            }  
            user\["activity"\]\["today"\]\["role\_ping\_joins"\] \+= 1  
            user\["activity"\]\["week"\]\["role\_ping\_joins"\] \+= 1

    save\_data()  
    await bot.process\_commands(message)

@bot.event  
async def on\_reaction\_add(reaction, user):  
    if user.bot:  
        return

    user\_id \= str(user.id)  
    ensure\_user(user\_id)

    users\_data\[user\_id\]\["activity"\]\["today"\]\["reaction\_count"\] \+= 1  
    users\_data\[user\_id\]\["activity"\]\["week"\]\["reaction\_count"\] \+= 1  
    users\_data\[user\_id\]\["last\_activity"\] \= datetime.utcnow().isoformat()

    save\_data()

@bot.event  
async def on\_voice\_state\_update(member, before, after):  
    if member.bot:  
        return

    user\_id \= str(member.id)  
    ensure\_user(user\_id)

    \# Joined a voice channel  
    if before.channel is None and after.channel is not None:  
        voice\_sessions\[user\_id\] \= {  
            "start": datetime.utcnow(),  
            "channel\_id": after.channel.id,  
            "role\_ping": False,  
            "from\_pings": set()  
        }  
        users\_data\[user\_id\]\["last\_activity"\] \= datetime.utcnow().isoformat()  
        reward\_voice\_ping\_response(user\_id, after.channel.id)  
        save\_data()

    \# Left a voice channel  
    elif before.channel is not None and after.channel is None:  
        if user\_id in voice\_sessions:  
            session \= voice\_sessions\[user\_id\]  
            duration \= (datetime.utcnow() \- session\["start"\]).total\_seconds() / 60

            users\_data\[user\_id\]\["activity"\]\["today"\]\["voice\_minutes"\] \+= duration  
            users\_data\[user\_id\]\["activity"\]\["week"\]\["voice\_minutes"\] \+= duration

            ch\_id \= str(session\["channel\_id"\])  
            if ch\_id not in users\_data\[user\_id\]\["activity"\]\["today"\]\["voice\_unique\_channels"\]:  
                users\_data\[user\_id\]\["activity"\]\["today"\]\["voice\_unique\_channels"\].append(ch\_id)  
            if ch\_id not in users\_data\[user\_id\]\["activity"\]\["week"\]\["voice\_unique\_channels"\]:  
                users\_data\[user\_id\]\["activity"\]\["week"\]\["voice\_unique\_channels"\].append(ch\_id)

            if session\["role\_ping"\]:  
                users\_data\[user\_id\]\["activity"\]\["today"\]\["role\_ping\_join\_minutes"\] \+= duration  
                users\_data\[user\_id\]\["activity"\]\["week"\]\["role\_ping\_join\_minutes"\] \+= duration

            \# 50% extra boost if stayed \>= 60 minutes after ping  
            stay\_minutes \= duration  
            if stay\_minutes \>= 60:  
                \# If user joined from any ping, apply one-time 50% boost  
                ensure\_price(user\_id)  
                current\_price \= prices\_data\[user\_id\]\["current"\]  
                boosted \= current\_price \* 1.50  
                boosted \= apply\_floor\_stall(current\_price, boosted)  
                update\_price\_record(user\_id, boosted)

            del voice\_sessions\[user\_id\]  
            save\_data()

    \# Switched channels  
    elif before.channel \!= after.channel and before.channel is not None and after.channel is not None:  
        if user\_id in voice\_sessions:  
            session \= voice\_sessions\[user\_id\]  
            duration \= (datetime.utcnow() \- session\["start"\]).total\_seconds() / 60

            users\_data\[user\_id\]\["activity"\]\["today"\]\["voice\_minutes"\] \+= duration  
            users\_data\[user\_id\]\["activity"\]\["week"\]\["voice\_minutes"\] \+= duration

            ch\_id \= str(session\["channel\_id"\])  
            if ch\_id not in users\_data\[user\_id\]\["activity"\]\["today"\]\["voice\_unique\_channels"\]:  
                users\_data\[user\_id\]\["activity"\]\["today"\]\["voice\_unique\_channels"\].append(ch\_id)  
            if ch\_id not in users\_data\[user\_id\]\["activity"\]\["week"\]\["voice\_unique\_channels"\]:  
                users\_data\[user\_id\]\["activity"\]\["week"\]\["voice\_unique\_channels"\].append(ch\_id)

            if session\["role\_ping"\]:  
                users\_data\[user\_id\]\["activity"\]\["today"\]\["role\_ping\_join\_minutes"\] \+= duration  
                users\_data\[user\_id\]\["activity"\]\["week"\]\["role\_ping\_join\_minutes"\] \+= duration

        voice\_sessions\[user\_id\] \= {  
            "start": datetime.utcnow(),  
            "channel\_id": after.channel.id,  
            "role\_ping": False,  
            "from\_pings": set()  
        }  
        users\_data\[user\_id\]\["last\_activity"\] \= datetime.utcnow().isoformat()  
        reward\_voice\_ping\_response(user\_id, after.channel.id)  
        save\_data()

@bot.event  
async def on\_member\_update(before: discord.Member, after: discord.Member):  
    \# Timeout detection (Discord uses timed\_out\_until in some libs)  
    if before.timed\_out\_until \!= after.timed\_out\_until and after.timed\_out\_until is not None:  
        user\_id \= str(after.id)  
        ensure\_price(user\_id)  
        current \= prices\_data\[user\_id\]\["current"\]  
        penalized \= current \* (1 \- DISCIPLINE\_PENALTY)  
        penalized \= max(penalized, MIN\_PRICE)  
        update\_price\_record(user\_id, penalized)  
        save\_data()

@bot.event  
async def on\_member\_ban(guild, user):  
    user\_id \= str(user.id)  
    if user\_id in prices\_data:  
        current \= prices\_data\[user\_id\]\["current"\]  
        penalized \= current \* (1 \- DISCIPLINE\_PENALTY)  
        penalized \= max(penalized, MIN\_PRICE)  
        update\_price\_record(user\_id, penalized)  
        save\_data()

\# \===== PERIODIC TASKS \=====

@tasks.loop(minutes=ACTIVITY\_TICK\_MINUTES)  
async def activity\_price\_step():  
    """  
    Periodic activity-based price tick, with stall near MIN\_PRICE.  
    """  
    now \= datetime.utcnow()  
    for user\_id in list(users\_data.keys()):  
        ensure\_price(user\_id)  
        current\_price \= prices\_data\[user\_id\]\["current"\]

        ret\_pct \= compute\_activity\_return(user\_id)  
        proposed\_price \= current\_price \* (1 \+ ret\_pct / 100.0)  
        new\_price \= apply\_floor\_stall(current\_price, proposed\_price)  
        update\_price\_record(user\_id, new\_price)

    save\_data()

@tasks.loop(minutes=5)  
async def inactivity\_price\_decay():  
    """  
    Decay prices for users inactive longer than INACTIVITY\_THRESHOLD.  
    """  
    now \= datetime.utcnow()  
    for user\_id, user in list(users\_data.items()):  
        last\_str \= user.get("last\_activity")  
        if not last\_str:  
            continue  
        try:  
            last \= datetime.fromisoformat(last\_str)  
        except Exception:  
            continue  
        if (now \- last).total\_seconds() \> INACTIVITY\_THRESHOLD:  
            ensure\_price(user\_id)  
            current \= prices\_data\[user\_id\]\["current"\]  
            proposed \= current \* (1 \- INACTIVITY\_DECAY)  
            new\_price \= apply\_floor\_stall(current, proposed)  
            update\_price\_record(user\_id, new\_price)

    save\_data()

@tasks.loop(minutes=5)  
async def short\_liquidation\_check():  
    """  
    Placeholder: existing short liquidation logic goes here.  
    """  
    \# Keep your existing liquidation code if you had it before.  
    pass

@tasks.loop(minutes=5)  
async def short\_freeze\_check():  
    """  
    Freeze shorts after SHORT\_FREEZE\_MINUTES.  
    """  
    now \= datetime.utcnow()  
    for user\_id, user in users\_data.items():  
        shorts \= user\["portfolio"\]\["short"\]  
        for target\_id, position in list(shorts.items()):  
            created\_str \= position.get("created\_at")  
            if not created\_str:  
                continue  
            try:  
                created \= datetime.fromisoformat(created\_str)  
            except Exception:  
                continue  
            age\_minutes \= (now \- created).total\_seconds() / 60  
            if age\_minutes \>= SHORT\_FREEZE\_MINUTES:  
                position\["frozen"\] \= True  
    save\_data()

@tasks.loop(minutes=15)  
async def vc\_extra\_boost\_step():  
    """  
    Apply 3% boosts every 15 minutes for extra VC responders beyond first 10,  
    while they remain within the 1.5h window and currently in VC.  
    """  
    now \= datetime.utcnow()  
    to\_delete \= \[\]  
    for user\_id, info in list(vc\_extra\_boosts.items()):  
        end\_time \= info\["end\_time"\]  
        last\_boost \= info\["last\_boost"\]

        if now \>= end\_time:  
            to\_delete.append(user\_id)  
            continue

        if (now \- last\_boost).total\_seconds() \< VC\_EXTRA\_BOOST\_INTERVAL\_SECONDS:  
            continue

        \# Only apply if they are currently in voice  
        if user\_id not in voice\_sessions:  
            continue

        ensure\_price(user\_id)  
        current \= prices\_data\[user\_id\]\["current"\]  
        proposed \= current \* 1.03  
        new\_price \= apply\_floor\_stall(current, proposed)  
        update\_price\_record(user\_id, new\_price)  
        vc\_extra\_boosts\[user\_id\]\["last\_boost"\] \= now

    for uid in to\_delete:  
        vc\_extra\_boosts.pop(uid, None)

    save\_data()

@tasks.loop(hours=1)  
async def monthly\_rollover\_check():  
    """  
    Placeholder: existing monthly rollover logic, if used.  
    """  
    \# Keep your existing rollover logic here if you had it.  
    pass

\# \===== COMMANDS: ECONOMY \=====

@bot.command(name='balance')  
async def balance(ctx):  
    """Check your cash balance and net worth."""  
    user\_id \= str(ctx.author.id)  
    ensure\_user(user\_id)  
    ensure\_fund(user\_id)

    cash \= users\_data\[user\_id\]\["cash\_balance"\]  
    net\_worth \= calculate\_net\_worth(user\_id)  
    fund\_cash \= funds\_data\[user\_id\]\["cash\_balance"\]

    users\_data\[user\_id\]\["net\_worth"\] \= net\_worth  
    save\_data()

    embed \= discord.Embed(title=f"💰 {ctx.author.display\_name}'s Balance", color=discord.Color.green())  
    embed.add\_field(name="Cash", value=f"${cash:,.2f}", inline=True)  
    embed.add\_field(name="Net Worth", value=f"${net\_worth:,.2f}", inline=True)  
    embed.add\_field(name="Hedge Fund", value=f"${fund\_cash:,.2f}", inline=True)

    await ctx.send(embed=embed, delete\_after=15)

@bot.command(name='mb')  
async def mb(ctx):  
    """Shortcut for $balance."""  
    await balance(ctx)

@bot.command(name='daily')  
async def daily(ctx):  
    """Claim your daily reward (500/day, 1000 on day 7 streak)."""  
    user\_id \= str(ctx.author.id)  
    ensure\_user(user\_id)

    user \= users\_data\[user\_id\]  
    last\_claim \= user\["daily"\]\["last\_claim"\]

    now \= datetime.utcnow()  
    can\_claim \= False

    if last\_claim is None:  
        can\_claim \= True  
        user\["daily"\]\["streak"\] \= 1  
    else:  
        last\_claim\_dt \= datetime.fromisoformat(last\_claim)  
        time\_since \= now \- last\_claim\_dt

        if time\_since \>= timedelta(days=1):  
            can\_claim \= True  
            if time\_since \< timedelta(days=2):  
                user\["daily"\]\["streak"\] \+= 1  
            else:  
                user\["daily"\]\["streak"\] \= 1

    if not can\_claim:  
        last\_claim\_dt \= datetime.fromisoformat(last\_claim)  
        next\_claim \= last\_claim\_dt \+ timedelta(days=1)  
        time\_left \= next\_claim \- now  
        hours \= int(time\_left.total\_seconds() // 3600\)  
        minutes \= int((time\_left.total\_seconds() % 3600\) // 60\)  
        await ctx.send(f"❌ You already claimed your daily reward\! Next claim in {hours}h {minutes}m.", delete\_after=15)  
        return

    reward \= DAILY\_REWARD  
    if user\["daily"\]\["streak"\] \== 7:  
        reward \= DAILY\_REWARD \+ STREAK\_BONUS  
        await ctx.send(f"🎉 \*\*7-day streak bonus\!\*\* You received ${reward:,.0f}\!", delete\_after=15)  
        user\["daily"\]\["streak"\] \= 0  
    else:  
        await ctx.send(f"✅ Daily reward claimed\! \+${reward:,.0f} (Streak: {user\['daily'\]\['streak'\]} days)", delete\_after=15)

    user\["cash\_balance"\] \+= reward  
    user\["daily"\]\["last\_claim"\] \= now.isoformat()  
    save\_data()

\# \===== COMMANDS: TRADING / PRICES \=====

@bot.command(name='price')  
async def price(ctx, target: discord.Member \= None):  
    """Check a user's stock price."""  
    if target is None:  
        target \= ctx.author

    target\_id \= str(target.id)  
    ensure\_price(target\_id)

    current\_price \= prices\_data\[target\_id\]\["current"\]  
    price\_change \= get\_24h\_price\_change(target\_id)  
    high\_24h \= prices\_data\[target\_id\]\["high\_24h"\]  
    low\_24h \= prices\_data\[target\_id\]\["low\_24h"\]  
    all\_time\_high \= prices\_data\[target\_id\].get("all\_time\_high", current\_price)

    embed \= discord.Embed(title=f"📊 {target.display\_name}'s Stock Price", color=discord.Color.blue())  
    embed.add\_field(name="Current Price", value=f"${current\_price:.2f}", inline=True)  
    embed.add\_field(name="24h Change", value=f"{price\_change:+.2f}%", inline=True)  
    embed.add\_field(name="24h High", value=f"${high\_24h:.2f}", inline=True)  
    embed.add\_field(name="24h Low", value=f"${low\_24h:.2f}", inline=True)  
    embed.add\_field(name="All-Time High", value=f"${all\_time\_high:.2f}", inline=True)

    await ctx.send(embed=embed, delete\_after=15)

@bot.command(name='ticker')  
async def ticker(ctx, target: discord.Member \= None):  
    """Alias for $price."""  
    await price(ctx, target)

@bot.command(name='my\_stock')  
async def my\_stock(ctx):  
    """Quick view of your own stock price."""  
    await price(ctx, ctx.author)

def trading\_allowed(ctx) \-\> Optional\[str\]:  
    """Return error message if trading not allowed, else None."""  
    now \= get\_now\_with\_offset()  
    if is\_sunday(now):  
        return "❌ The market is closed on Sundays for trading."  
    if not is\_market\_open(now):  
        return "❌ The market is closed. You can only trade during market hours."  
    return None

@bot.command(name='buy')  
async def buy(ctx, target: discord.Member, shares: int):  
    """Buy shares of another user's stock."""  
    if shares \<= 0:  
        await ctx.send("❌ Shares must be positive.", delete\_after=15)  
        return

    now \= get\_now\_with\_offset()  
    \# Buy allowed on Sunday intentionally  
    if not is\_sunday(now):  
        err \= trading\_allowed(ctx)  
        if err:  
            await ctx.send(err, delete\_after=15)  
            return

    user\_id \= str(ctx.author.id)  
    target\_id \= str(target.id)

    if user\_id \== target\_id:  
        await ctx.send("❌ You cannot buy your own stock.", delete\_after=15)  
        return

    ensure\_user(user\_id)  
    ensure\_price(target\_id)

    current\_price \= prices\_data\[target\_id\]\["current"\]  
    cost \= current\_price \* shares

    if users\_data\[user\_id\]\["cash\_balance"\] \< cost:  
        await ctx.send(  
            f"❌ Insufficient funds\! You need ${cost:,.2f} but have ${users\_data\[user\_id\]\['cash\_balance'\]:,.2f}.",  
            delete\_after=15  
        )  
        return

    users\_data\[user\_id\]\["cash\_balance"\] \-= cost

    portfolio \= users\_data\[user\_id\]\["portfolio"\]\["long"\]  
    if target\_id in portfolio:  
        old\_shares \= portfolio\[target\_id\]\["shares"\]  
        old\_avg \= portfolio\[target\_id\]\["avg\_entry"\]  
        new\_shares \= old\_shares \+ shares  
        new\_avg \= ((old\_shares \* old\_avg) \+ (shares \* current\_price)) / new\_shares  
        portfolio\[target\_id\]\["shares"\] \= new\_shares  
        portfolio\[target\_id\]\["avg\_entry"\] \= new\_avg  
    else:  
        portfolio\[target\_id\] \= {  
            "shares": shares,  
            "avg\_entry": current\_price  
        }

    apply\_trade\_price\_impact(target\_id, shares, is\_buy=True)  
    save\_data()

    await ctx.send(  
        f"✅ Bought {shares} shares of {target.display\_name} for ${cost:,.2f} at ${current\_price:.2f}/share.",  
        delete\_after=15  
    )

@bot.command(name='sell')  
async def sell(ctx, target: discord.Member, shares: int):  
    """Sell shares of another user's stock."""  
    if shares \<= 0:  
        await ctx.send("❌ Shares must be positive.", delete\_after=15)  
        return

    err \= trading\_allowed(ctx)  
    if err:  
        await ctx.send(err, delete\_after=15)  
        return

    user\_id \= str(ctx.author.id)  
    target\_id \= str(target.id)

    ensure\_user(user\_id)  
    ensure\_price(target\_id)

    portfolio \= users\_data\[user\_id\]\["portfolio"\]\["long"\]

    if target\_id not in portfolio or portfolio\[target\_id\]\["shares"\] \< shares:  
        owned \= portfolio.get(target\_id, {}).get("shares", 0\)  
        await ctx.send(  
            f"❌ Insufficient shares\! You own {owned} shares of {target.display\_name}.",  
            delete\_after=15  
        )  
        return

    current\_price \= prices\_data\[target\_id\]\["current"\]  
    revenue \= current\_price \* shares

    users\_data\[user\_id\]\["cash\_balance"\] \+= revenue

    portfolio\[target\_id\]\["shares"\] \-= shares  
    if portfolio\[target\_id\]\["shares"\] \== 0:  
        del portfolio\[target\_id\]

    apply\_trade\_price\_impact(target\_id, shares, is\_buy=False)  
    save\_data()

    await ctx.send(  
        f"✅ Sold {shares} shares of {target.display\_name} for ${revenue:,.2f} at ${current\_price:.2f}/share.",  
        delete\_after=15  
    )

@bot.command(name='short')  
async def short(ctx, target: discord.Member, shares: int):  
    """Short sell another user's stock."""  
    if shares \<= 0:  
        await ctx.send("❌ Shares must be positive.", delete\_after=15)  
        return

    err \= trading\_allowed(ctx)  
    if err:  
        await ctx.send(err, delete\_after=15)  
        return

    user\_id \= str(ctx.author.id)  
    target\_id \= str(target.id)

    if user\_id \== target\_id:  
        await ctx.send("❌ You cannot short your own stock.", delete\_after=15)  
        return

    ensure\_user(user\_id)  
    ensure\_price(target\_id)  
    ensure\_fund(user\_id)

    current\_price \= prices\_data\[target\_id\]\["current"\]  
    notional \= current\_price \* shares

    cash\_available \= users\_data\[user\_id\]\["cash\_balance"\]  
    fund\_cash \= funds\_data\[user\_id\]\["cash\_balance"\]  
    fund\_available \= fund\_cash \* 0.5  
    total\_collateral \= cash\_available \+ fund\_available

    if total\_collateral \< notional:  
        await ctx.send(  
            f"❌ Insufficient collateral\! Need ${notional:,.2f}, have ${total\_collateral:,.2f} (cash \+ 50% of fund).",  
            delete\_after=15  
        )  
        return

    locked\_cash \= min(cash\_available, notional)  
    locked\_fund \= min(fund\_available, notional \- locked\_cash)

    users\_data\[user\_id\]\["cash\_balance"\] \-= locked\_cash  
    funds\_data\[user\_id\]\["cash\_balance"\] \-= locked\_fund

    portfolio \= users\_data\[user\_id\]\["portfolio"\]\["short"\]  
    now\_str \= datetime.utcnow().isoformat()

    if target\_id in portfolio:  
        position \= portfolio\[target\_id\]  
        if position.get("frozen"):  
            await ctx.send("❌ This short position is frozen and cannot be modified.", delete\_after=15)  
            return

        old\_shares \= position\["shares"\]  
        old\_entry \= position\["entry\_price"\]  
        old\_locked\_cash \= position\["locked\_cash"\]  
        old\_locked\_fund \= position\["locked\_fund"\]

        new\_shares \= old\_shares \+ shares  
        new\_entry \= ((old\_shares \* old\_entry) \+ (shares \* current\_price)) / new\_shares

        position\["shares"\] \= new\_shares  
        position\["entry\_price"\] \= new\_entry  
        position\["locked\_cash"\] \= old\_locked\_cash \+ locked\_cash  
        position\["locked\_fund"\] \= old\_locked\_fund \+ locked\_fund  
    else:  
        portfolio\[target\_id\] \= {  
            "shares": shares,  
            "entry\_price": current\_price,  
            "locked\_cash": locked\_cash,  
            "locked\_fund": locked\_fund,  
            "created\_at": now\_str,  
            "frozen": False  
        }

    apply\_trade\_price\_impact(target\_id, shares, is\_buy=False)  
    set\_trade\_time(user\_id)  
    save\_data()

    await ctx.send(  
        f"✅ Shorted {shares} shares of {target.display\_name} at ${current\_price:.2f}/share. Locked ${locked\_cash \+ locked\_fund:,.2f} collateral.",  
        delete\_after=15  
    )

@bot.command(name='cover')  
async def cover(ctx, target: discord.Member, shares: int):  
    """Cover a short position."""  
    if shares \<= 0:  
        await ctx.send("❌ Shares must be positive.", delete\_after=15)  
        return

    err \= trading\_allowed(ctx)  
    if err:  
        await ctx.send(err, delete\_after=15)  
        return

    user\_id \= str(ctx.author.id)  
    target\_id \= str(target.id)

    ensure\_user(user\_id)  
    ensure\_price(target\_id)  
    ensure\_fund(user\_id)

    portfolio \= users\_data\[user\_id\]\["portfolio"\]\["short"\]

    if target\_id not in portfolio or portfolio\[target\_id\]\["shares"\] \< shares:  
        owned \= portfolio.get(target\_id, {}).get("shares", 0\)  
        await ctx.send(  
            f"❌ Insufficient short position\! You have shorted {owned} shares of {target.display\_name}.",  
            delete\_after=15  
        )  
        return

    position \= portfolio\[target\_id\]  
    if position.get("frozen"):  
        await ctx.send("❌ This short position is frozen and cannot be covered right now.", delete\_after=15)  
        return

    current\_price \= prices\_data\[target\_id\]\["current"\]  
    cost \= current\_price \* shares

    if users\_data\[user\_id\]\["cash\_balance"\] \< cost:  
        await ctx.send(  
            f"❌ Insufficient cash to cover\! Need ${cost:,.2f}, have ${users\_data\[user\_id\]\['cash\_balance'\]:,.2f}.",  
            delete\_after=15  
        )  
        return

    users\_data\[user\_id\]\["cash\_balance"\] \-= cost

    entry\_price \= position\["entry\_price"\]  
    pnl \= (entry\_price \- current\_price) \* shares

    proportion \= shares / position\["shares"\]  
    released\_cash \= position\["locked\_cash"\] \* proportion  
    released\_fund \= position\["locked\_fund"\] \* proportion

    users\_data\[user\_id\]\["cash\_balance"\] \+= released\_cash  
    funds\_data\[user\_id\]\["cash\_balance"\] \+= released\_fund

    if pnl \> 0:  
        users\_data\[user\_id\]\["cash\_balance"\] \+= pnl

    position\["shares"\] \-= shares  
    position\["locked\_cash"\] \-= released\_cash  
    position\["locked\_fund"\] \-= released\_fund

    if position\["shares"\] \== 0:  
        del portfolio\[target\_id\]

    set\_trade\_time(user\_id)  
    save\_data()

    pnl\_text \= f"+${pnl:,.2f}" if pnl \> 0 else f"-${abs(pnl):,.2f}"  
    await ctx.send(  
        f"✅ Covered {shares} shares of {target.display\_name} at ${current\_price:.2f}/share. PnL: {pnl\_text}.",  
        delete\_after=15  
    )

@bot.command(name='portfolio')  
async def portfolio\_cmd(ctx, target: discord.Member \= None):  
    """View your portfolio or another user's portfolio."""  
    if target is None:  
        target \= ctx.author

    target\_id \= str(target.id)  
    ensure\_user(target\_id)

    user \= users\_data\[target\_id\]

    embed \= discord.Embed(title=f"📈 {target.display\_name}'s Portfolio", color=discord.Color.gold())

    long\_text \= ""  
    for stock\_id, position in user\["portfolio"\]\["long"\].items():  
        member \= ctx.guild.get\_member(int(stock\_id))  
        if member:  
            ensure\_price(stock\_id)  
            current\_price \= prices\_data\[stock\_id\]\["current"\]  
            value \= position\["shares"\] \* current\_price  
            pnl \= (current\_price \- position\["avg\_entry"\]) \* position\["shares"\]  
            pnl\_pct \= ((current\_price \- position\["avg\_entry"\]) / position\["avg\_entry"\]) \* 100 if position\["avg\_entry"\] \> 0 else 0  
            long\_text \+= (  
                f"\*\*{member.display\_name}\*\*: {position\['shares'\]} @ ${position\['avg\_entry'\]:.2f} "  
                f"(Now: ${current\_price:.2f}) | Value: ${value:.2f} | PnL: ${pnl:+.2f} ({pnl\_pct:+.1f}%)\\n"  
            )

    if not long\_text:  
        long\_text \= "No long positions"

    embed.add\_field(name="Long Positions", value=long\_text, inline=False)

    short\_text \= ""  
    for stock\_id, position in user\["portfolio"\]\["short"\].items():  
        member \= ctx.guild.get\_member(int(stock\_id))  
        if member:  
            ensure\_price(stock\_id)  
            current\_price \= prices\_data\[stock\_id\]\["current"\]  
            pnl \= (position\["entry\_price"\] \- current\_price) \* position\["shares"\]  
            pnl\_pct \= ((position\["entry\_price"\] \- current\_price) / position\["entry\_price"\]) \* 100 if position\["entry\_price"\] \> 0 else 0  
            frozen\_flag \= " (FROZEN)" if position.get("frozen") else ""  
            short\_text \+= (  
                f"\*\*{member.display\_name}\*\*: {position\['shares'\]} @ ${position\['entry\_price'\]:.2f} "  
                f"(Now: ${current\_price:.2f}) | PnL: ${pnl:+.2f} ({pnl\_pct:+.1f}%){frozen\_flag}\\n"  
            )

    if not short\_text:  
        short\_text \= "No short positions"

    embed.add\_field(name="Short Positions", value=short\_text, inline=False)

    await ctx.send(embed=embed, delete\_after=15)

@bot.command(name='pf')  
async def pf(ctx, target: discord.Member \= None):  
    await portfolio\_cmd(ctx, target)

@bot.command(name='mp')  
async def mp(ctx, target: discord.Member \= None):  
    await portfolio\_cmd(ctx, target or ctx.author)

\# \===== COMMANDS: HEDGE FUNDS \=====

@bot.command(name='fund')  
async def fund(ctx, action: str \= None, \*args):  
    """Manage your hedge fund. Usage: $fund create/info/withdraw/send\_events"""  
    if action is None:  
        await ctx.send(  
            "Usage: $fund create \[name\], $fund info \[@user\], $fund withdraw \<amount\>, $fund send\_events \<amount\>",  
            delete\_after=15  
        )  
        return

    action \= action.lower()

    if action \== "create":  
        user\_id \= str(ctx.author.id)  
        ensure\_fund(user\_id)

        if args:  
            name \= " ".join(args)  
            funds\_data\[user\_id\]\["name"\] \= name  
            save\_data()  
            await ctx.send(f"✅ Hedge fund renamed to: \*\*{name}\*\*", delete\_after=15)  
        else:  
            await ctx.send(f"✅ Hedge fund created: \*\*{funds\_data\[user\_id\]\['name'\]}\*\*", delete\_after=15)

    elif action \== "info":  
        if args and ctx.message.mentions:  
            target \= ctx.message.mentions\[0\]  
        else:  
            target \= ctx.author

        target\_id \= str(target.id)  
        ensure\_fund(target\_id)

        fund\_obj \= funds\_data\[target\_id\]  
        penalty\_apr \= get\_user\_penalty\_apr(target\_id)  
        effective\_apy \= max(0.0, HEDGE\_FUND\_BASE\_APY \- penalty\_apr)

        embed \= discord.Embed(title=f"🏦 {fund\_obj\['name'\]}", color=discord.Color.purple())  
        embed.add\_field(name="Manager", value=target.mention, inline=True)  
        embed.add\_field(name="Cash Balance", value=f"${fund\_obj\['cash\_balance'\]:,.2f}", inline=True)  
        embed.add\_field(name="Investors", value=str(len(fund\_obj\['investors'\])), inline=True)  
        embed.add\_field(name="Base APY", value=f"{HEDGE\_FUND\_BASE\_APY\*100:.1f}%", inline=True)  
        embed.add\_field(name="Penalty APR", value=f"{penalty\_apr\*100:.1f}%", inline=True)  
        embed.add\_field(name="Effective APY", value=f"{effective\_apy\*100:.1f}%", inline=True)

        await ctx.send(embed=embed, delete\_after=15)

    elif action \== "withdraw":  
        if not args:  
            await ctx.send("Usage: $fund withdraw \<amount\>", delete\_after=15)  
            return  
        try:  
            amount \= float(args\[0\])  
        except ValueError:  
            await ctx.send("❌ Invalid amount.", delete\_after=15)  
            return  
        if amount \<= 0:  
            await ctx.send("❌ Amount must be positive.", delete\_after=15)  
            return

        user\_id \= str(ctx.author.id)  
        ensure\_user(user\_id)  
        ensure\_fund(user\_id)

        fund\_obj \= funds\_data\[user\_id\]

        if fund\_obj\["cash\_balance"\] \< amount:  
            await ctx.send(  
                f"❌ Insufficient fund balance\! Available: ${fund\_obj\['cash\_balance'\]:,.2f}.",  
                delete\_after=15  
            )  
            return

        \# Apply early withdrawal penalty logic (unless this is month-end rollover)  
        now \= datetime.utcnow()  
        if now.day \!= 1:  \# treat anything not on the 1st as "early"  
            apply\_early\_withdraw\_penalty(user\_id)

        fund\_obj\["cash\_balance"\] \-= amount  
        users\_data\[user\_id\]\["cash\_balance"\] \+= amount

        save\_data()

        await ctx.send(  
            f"✅ Withdrew ${amount:,.2f} from your hedge fund to your trading account.",  
            delete\_after=15  
        )

    elif action \== "send\_events":  
        if not args:  
            await ctx.send("Usage: $fund send\_events \<amount\>", delete\_after=15)  
            return  
        try:  
            amount \= float(args\[0\])  
        except ValueError:  
            await ctx.send("❌ Invalid amount.", delete\_after=15)  
            return  
        if amount \<= 0:  
            await ctx.send("❌ Amount must be positive.", delete\_after=15)  
            return

        user\_id \= str(ctx.author.id)  
        ensure\_user(user\_id)  
        ensure\_fund(user\_id)  
        ensure\_events\_wallet()

        fund\_obj \= funds\_data\[user\_id\]  
        events\_fund \= funds\_data\[EVENTS\_WALLET\_USER\_ID\]

        if fund\_obj\["cash\_balance"\] \< amount:  
            await ctx.send(  
                f"❌ Insufficient fund balance\! Available: ${fund\_obj\['cash\_balance'\]:,.2f}.",  
                delete\_after=15  
            )  
            return

        \# Events transfer: no APY penalty  
        fund\_obj\["cash\_balance"\] \-= amount  
        events\_fund\["cash\_balance"\] \+= amount

        save\_data()

        await ctx.send(  
            f"✅ Sent ${amount:,.2f} from your hedge fund to the Events Wallet.",  
            delete\_after=15  
        )

\# \===== COMMANDS: TRENDING / LOSERS / STATS \=====

@bot.command(name='trending')  
async def trending(ctx):  
    """Show top trending tickers with rank, price, and 24h change."""  
    \# Build trending scores  
    scores \= \[\]  
    all\_scores \= \[\]  
    for user\_id, user in users\_data.items():  
        week\_activity \= user\["activity"\]\["week"\]  
        score \= calculate\_trending\_score(week\_activity)  
        scores.append((user\_id, score))  
        all\_scores.append(score)

    scores \= \[s for s in scores if s\[1\] \> 0\]  
    scores.sort(key=lambda x: x\[1\], reverse=True)  
    top \= scores\[:15\]

    if not top:  
        await ctx.send("No trending tickers yet.", delete\_after=15)  
        return

    embed \= discord.Embed(title="📈 Trending Tickers", color=discord.Color.green())  
    lines \= \[\]  
    rank \= 1  
    for user\_id, score in top:  
        member \= ctx.guild.get\_member(int(user\_id))  
        if not member:  
            continue  
        ensure\_price(user\_id)  
        current\_price \= prices\_data\[user\_id\]\["current"\]  
        change\_24h \= get\_24h\_price\_change(user\_id)  
        lines.append(f"\#{rank} {member.display\_name} – ${current\_price:.2f} ({change\_24h:+.2f}%)")  
        rank \+= 1

    embed.description \= "\\n".join(lines)  
    await ctx.send(embed=embed, delete\_after=15)

@bot.command(name='mystats')  
async def mystats(ctx):  
    """Show your activity stats and which boosts you've hit this week."""  
    user\_id \= str(ctx.author.id)  
    ensure\_user(user\_id)  
    user \= users\_data\[user\_id\]

    today \= user\["activity"\]\["today"\]  
    week \= user\["activity"\]\["week"\]

    embed \= discord.Embed(title=f"📊 {ctx.author.display\_name}'s Stats", color=discord.Color.orange())

    today\_text \= (  
        f"Text msgs: {today\['text\_msgs'\]}\\n"  
        f"Media msgs: {today\['media\_msgs'\]}\\n"  
        f"Voice minutes: {today\['voice\_minutes'\]:.1f}\\n"  
        f"Reactions: {today\['reaction\_count'\]}\\n"  
        f"Replies: {today\['reply\_count'\]}\\n"  
    )  
    week\_text \= (  
        f"Text msgs: {week\['text\_msgs'\]}\\n"  
        f"Media msgs: {week\['media\_msgs'\]}\\n"  
        f"Voice minutes: {week\['voice\_minutes'\]:.1f}\\n"  
        f"Reactions: {week\['reaction\_count'\]}\\n"  
        f"Replies: {week\['reply\_count'\]}\\n"  
    )

    \# Boost activities list  
    boosts \= \[\]  
    if week\["media\_msgs"\] \> 0:  
        boosts.append("Sent media this week")  
    if week\["role\_ping\_joins"\] \> 0:  
        boosts.append("Started or responded to VC pings")  
    if week\["voice\_minutes"\] \>= 60:  
        boosts.append("Spent 60+ minutes in VC")  
    if week\["role\_ping\_join\_minutes"\] \> 0:  
        boosts.append("Joined VC after pings (speed/bonus points)")  
    \# Extra VC 3% boosts are not directly counted yet; they come from extra\_joiners logic

    boosts\_text \= "\\n".join(f"- {b}" for b in boosts) if boosts else "No boost activities recorded yet."

    embed.add\_field(name="Today", value=today\_text, inline=True)  
    embed.add\_field(name="This Week", value=week\_text, inline=True)  
    embed.add\_field(name="Boost activities completed", value=boosts\_text, inline=False)

    await ctx.send(embed=embed, delete\_after=15)

\# \===== OPT-IN / OPT-OUT & INTRO \=====

def get\_intro\_embed() \-\> discord.Embed:  
    embed \= discord.Embed(  
        title="Welcome to the Slut Friends Stock XxXChange",  
        description=(  
            "Opt-in to receive $10,000 starting. Come back every day to get a daily bonus. "  
            "Start a hedge fund to cash in your earnings for sexy perks, prizes, and more."  
        ),  
        color=discord.Color.pink()  
    )  
    return embed

@bot.command(name='optin')  
async def optin(ctx):  
    """Opt in to the Slut Friends Stock XxXChange."""  
    user\_id \= str(ctx.author.id)  
    ensure\_user(user\_id)

    users\_data\[user\_id\]\["opt\_in"\] \= True

    \# Show intro once  
    if not users\_data\[user\_id\].get("intro\_shown", False):  
        intro\_embed \= get\_intro\_embed()  
        await ctx.send(embed=intro\_embed, delete\_after=15)  
        users\_data\[user\_id\]\["intro\_shown"\] \= True

    save\_data()  
    await ctx.send("✅ You are opted \*\*in\*\* to the Slut Friends Stock XxXChange.", delete\_after=15)

@bot.command(name='optout')  
async def optout(ctx):  
    """Opt out of perks (still tradable as a stock)."""  
    user\_id \= str(ctx.author.id)  
    ensure\_user(user\_id)

    users\_data\[user\_id\]\["opt\_in"\] \= False  
    save\_data()  
    await ctx.send(  
        "✅ You are opted \*\*out\*\*. You're still tradable as a stock, but you won't receive perks or cash-outs.",  
        delete\_after=15  
    )

@bot.command(name='game\_intro')  
@commands.has\_permissions(manage\_guild=True)  
async def game\_intro(ctx):  
    """Post the game intro embed."""  
    embed \= get\_intro\_embed()  
    await ctx.send(embed=embed)

\# \===== HELP COMMAND (NOT EPHEMERAL) \=====

@bot.command(name='help')  
async def help\_cmd(ctx):  
    """Show help for Slut Friends Stock XxXChange commands."""  
    embed \= discord.Embed(title="Slut Friends Stock XxXChange – Help", color=discord.Color.blurple())  
    embed.add\_field(  
        name="Core",  
        value=(  
            "\`$optin\`, \`$optout\`, \`$balance\`, \`$daily\`, \`$price\`, \`$ticker\`, \`$my\_stock\`,\\n"  
            "\`$buy\`, \`$sell\`, \`$short\`, \`$cover\`, \`$portfolio\`/\`$pf\`/\`$mp\`"  
        ),  
        inline=False  
    )  
    embed.add\_field(  
        name="Funds",  
        value="\`$fund create\`, \`$fund info\`, \`$fund withdraw\`, \`$fund send\_events\`",  
        inline=False  
    )  
    embed.add\_field(  
        name="Stats & Trends",  
        value="\`$trending\`, \`$mystats\`",  
        inline=False  
    )  
    embed.add\_field(  
        name="Admin",  
        value="\`$game\_intro\` (post intro message)",  
        inline=False  
    )  
    await ctx.send(embed=embed)

\# \===== RUN BOT \=====

if \_\_name\_\_ \== "\_\_main\_\_":  
    token \= os.getenv("DISCORD\_TOKEN")  
    if not token:  
        print("ERROR: DISCORD\_TOKEN environment variable not set\!")  
        exit(1)

    bot.run(token)

