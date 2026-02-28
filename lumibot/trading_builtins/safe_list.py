from _thread import RLock as rlock_type


_MISSING = object()


class SafeList:
    def __init__(self, lock, initial=None):
        # PERF: backtesting is single-threaded; allow `lock=None` to skip lock overhead.
        if lock is not None and not isinstance(lock, rlock_type):
            raise ValueError("lock must be a threading.RLock")

        if initial is None:
            initial = []
        self.__lock = lock
        self.__items = initial

    def __repr__(self):
        return repr(self.__items)

    def __bool__(self):
        lock = self.__lock
        if lock is None:
            return bool(self.__items)
        with lock:
            return bool(self.__items)

    def __len__(self):
        lock = self.__lock
        if lock is None:
            return len(self.__items)
        with lock:
            return len(self.__items)

    def __iter__(self):
        lock = self.__lock
        if lock is None:
            return iter(self.__items)
        with lock:
            return iter(self.__items)

    def __contains__(self, val):
        lock = self.__lock
        if lock is None:
            return val in self.__items
        with lock:
            return val in self.__items

    def __getitem__(self, n):
        lock = self.__lock
        if lock is None:
            return self.__items[n]
        with lock:
            return self.__items[n]

    def __setitem__(self, n, val):
        lock = self.__lock
        if lock is None:
            self.__items[n] = val
            return
        with lock:
            self.__items[n] = val

    def __add__(self, val):
        lock = self.__lock
        if lock is None:
            result = SafeList(None)
            result.__items = list(set(self.__items + val.__items))
            return result
        with lock:
            result = SafeList(lock)
            result.__items = list(set(self.__items + val.__items))
            return result

    def append(self, value):
        lock = self.__lock
        if lock is None:
            self.__items.append(value)
            return
        with lock:
            self.__items.append(value)

    def remove(self, value, key=None):
        lock = self.__lock
        if lock is None:
            if key is None:
                self.__items.remove(value)
                return
            if not isinstance(key, str):
                raise ValueError(f"key must be a string, received {key} of type {type(key)}")
            # PERF: key-based removals are heavily used in backtesting order tracking lists
            # (e.g., `key="identifier"`). Avoid rebuilding the full list each time.
            if key == "identifier":
                for idx, item in enumerate(self.__items):
                    item_identifier = getattr(item, "_identifier", _MISSING)
                    if item_identifier is _MISSING:
                        item_identifier = getattr(item, "identifier", _MISSING)
                    if item_identifier == value:
                        del self.__items[idx]
                        break
                return

            for idx, item in enumerate(self.__items):
                if getattr(item, key) == value:
                    del self.__items[idx]
                    break
            return

        with lock:
            if key is None:
                self.__items.remove(value)
            else:
                if not isinstance(key, str):
                    raise ValueError(f"key must be a string, received {key} of type {type(key)}")
                # PERF: key-based removals are heavily used in backtesting order tracking lists
                # (e.g., `key=\"identifier\"`). Avoid rebuilding the full list each time.
                if key == "identifier":
                    for idx, item in enumerate(self.__items):
                        item_identifier = getattr(item, "_identifier", _MISSING)
                        if item_identifier is _MISSING:
                            item_identifier = getattr(item, "identifier", _MISSING)
                        if item_identifier == value:
                            del self.__items[idx]
                            break
                else:
                    for idx, item in enumerate(self.__items):
                        if getattr(item, key) == value:
                            del self.__items[idx]
                            break

    def extend(self, value):
        lock = self.__lock
        if lock is None:
            self.__items.extend(value)
            return
        with lock:
            self.__items.extend(value)

    def get_list(self):
        lock = self.__lock
        if lock is None:
            return self.__items
        with lock:
            return self.__items

    def remove_all(self):
        lock = self.__lock
        if lock is None:
            for item in self.__items:
                self.remove(item)
            return
        with lock:
            for item in self.__items:
                self.remove(item)

    def trim_to_last(self, keep_last: int) -> int:
        """Keep only the last `keep_last` items (drop the oldest).

        This is a performance primitive used by backtesting to avoid O(n log n) sorts and repeated
        `list.remove()` scans when enforcing simple retention policies on append-only event lists.
        """
        keep_last = int(keep_last or 0)
        lock = self.__lock
        if lock is None:
            if keep_last <= 0:
                removed = len(self.__items)
                self.__items = []
                return removed
            if len(self.__items) <= keep_last:
                return 0
            removed = len(self.__items) - keep_last
            self.__items = self.__items[-keep_last:]
            return removed

        with lock:
            if keep_last <= 0:
                removed = len(self.__items)
                self.__items = []
                return removed
            if len(self.__items) <= keep_last:
                return 0
            removed = len(self.__items) - keep_last
            self.__items = self.__items[-keep_last:]
            return removed


class SafeOrderDict:
    """Dict-backed SafeList variant for order tracking buckets.

    WHY: Backtests remove orders by `identifier` extremely frequently. List-backed removals are
    O(n) scans and show up as a dominant CPU cost in high-churn minute backtests.

    This container stores orders in an insertion-ordered dict keyed by `order.identifier`, giving:
    - O(1) remove-by-identifier
    - O(1) contains-by-identifier
    - stable iteration order (dict insertion order)

    NOTE: This is intended for backtesting-only order buckets. It does not provide list indexing.
    """

    def __init__(self, lock=None, initial=None):
        if lock is not None and not isinstance(lock, rlock_type):
            raise ValueError("lock must be a threading.RLock")
        self.__lock = lock
        self.__items: dict[str, object] = {}
        if initial:
            for item in initial:
                self.append(item)

    @staticmethod
    def _identifier_for(item):
        identifier = getattr(item, "_identifier", _MISSING)
        if identifier is not _MISSING:
            return identifier
        identifier = getattr(item, "identifier", _MISSING)
        if identifier is not _MISSING:
            return identifier
        return None

    def __repr__(self):
        return repr(list(self.__items.values()))

    def __bool__(self):
        lock = self.__lock
        if lock is None:
            return bool(self.__items)
        with lock:
            return bool(self.__items)

    def __len__(self):
        lock = self.__lock
        if lock is None:
            return len(self.__items)
        with lock:
            return len(self.__items)

    def __iter__(self):
        lock = self.__lock
        if lock is None:
            return iter(self.__items.values())
        with lock:
            return iter(list(self.__items.values()))

    def __contains__(self, val):
        lock = self.__lock
        if lock is None:
            return self._contains_unlocked(val)
        with lock:
            return self._contains_unlocked(val)

    def _contains_unlocked(self, val):
        if isinstance(val, str):
            return val in self.__items
        identifier = self._identifier_for(val)
        if identifier is None:
            return False
        return identifier in self.__items

    def append(self, value):
        identifier = self._identifier_for(value)
        if identifier is None:
            raise ValueError("SafeOrderDict items must have an identifier")
        lock = self.__lock
        if lock is None:
            self.__items[str(identifier)] = value
            return
        with lock:
            self.__items[str(identifier)] = value

    def remove(self, value, key=None):
        lock = self.__lock
        if lock is None:
            return self._remove_unlocked(value, key=key)
        with lock:
            return self._remove_unlocked(value, key=key)

    def _remove_unlocked(self, value, key=None):
        if key is None:
            if isinstance(value, str):
                self.__items.pop(value, None)
                return
            identifier = self._identifier_for(value)
            if identifier is None:
                return
            self.__items.pop(str(identifier), None)
            return

        if key == "identifier":
            self.__items.pop(str(value), None)
            return

        # Fallback: scan by arbitrary key (should be rare for order buckets).
        if not isinstance(key, str):
            raise ValueError(f"key must be a string, received {key} of type {type(key)}")
        for identifier, item in list(self.__items.items()):
            if getattr(item, key, _MISSING) == value:
                self.__items.pop(identifier, None)
                break

    def extend(self, value):
        lock = self.__lock
        if lock is None:
            for item in value:
                self.append(item)
            return
        with lock:
            for item in value:
                self.append(item)

    def get_list(self):
        lock = self.__lock
        if lock is None:
            return self.__items.values()
        with lock:
            return list(self.__items.values())
