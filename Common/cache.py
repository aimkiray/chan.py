import inspect
import types


class make_cache:
    """按实例缓存的零参方法装饰器。

    约定（被装饰方法必须形如 `def f(self)`，装饰时即校验签名）：
    - 带参调用一律抛 TypeError（不论缓存是否已命中，行为一致，
      不会出现"首次带参 TypeError、二次静默返回旧值"的不一致）；
    - 缓存存放在 instance._memoize_cache，随实例回收，无无界增长；
      实例可借 clean_cache()（约定接口）主动失效；
    - 非线程安全：并发下至多重复计算，结果仍一致（良性竞态）。
    """

    def __init__(self, func):
        self.func = func

        fargspec = inspect.getfullargspec(func)
        if len(fargspec.args) != 1 or fargspec.args[0] != "self":
            raise TypeError("@make_cache must decorate a `(self)` zero-arg method")

        # set key for this function
        self.func_key = str(func)

    def __get__(self, instance, cls=None):
        if instance is None:
            # 类访问返回描述符本身（与 property/cached_property 一致），
            # 使 getattr/hasattr 语义正常，不再抛裸 Exception
            return self

        if not hasattr(instance, "_memoize_cache"):
            setattr(instance, "_memoize_cache", {})

        return types.MethodType(self, instance)

    def __call__(self, *args, **kwargs):
        if len(args) != 1 or kwargs:
            raise TypeError(
                f"{self.func.__name__}() is a zero-arg cached method, extra args are not supported"
            )
        instance = args[0]
        cache = getattr(instance, "_memoize_cache", None)
        if cache is None:
            cache = {}
            setattr(instance, "_memoize_cache", cache)

        if self.func_key in cache:
            return cache[self.func_key]

        result = self.func(instance)
        cache[self.func_key] = result
        return result
