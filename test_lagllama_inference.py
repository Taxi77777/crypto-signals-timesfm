import sys
import inspect
try:
    import gluonts.time_feature.lag as lag_module
    print("Source of get_lags_for_frequency:")
    print(inspect.getsource(lag_module.get_lags_for_frequency))
except Exception as e:
    print("Erreur:", e)
sys.exit(0)
