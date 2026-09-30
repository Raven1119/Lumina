"""Closed event routing for Organ A, independent of the retired Stage1 chain."""
# Internal dialogue events are transport, not new autonomous wake-up sources.
ROUTES = {
    'user.message': ('mind', 10),
    'mind.say': ('language', 0),
    'mind.trace': ('language', 0),
    'language.said': ('receipt', 0),
    'mind.spoke': ('dream', 20),
    'agent.question': ('mind', 20),
    'agent.report': ('mind', 30),
    'mind.spawn': ('execution', 0),
    'mind.reply': ('execution', 0),
    'mind.cancel': ('execution', 0),
    'mind.hold': ('execution', 0),
    'guard.return': ('execution', 0),
}

def route(kind):
    return ROUTES[kind]
