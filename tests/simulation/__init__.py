"""Full-stack simulation tests driven by YAML scenario configs.

Not true end-to-end: no bot logs in and no gateway connects. The whole
application stack below the Discord adapter boundary runs as production code;
only the gateway, the clock, and the database are replaced by doubles.
"""
