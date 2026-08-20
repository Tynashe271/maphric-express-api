"""Ownership scoping shared by the customer-facing viewsets."""


def scope_to_user(queryset, user, field='user'):
    """Limit ``queryset`` to the rows owned by ``user``; staff see everything."""
    if getattr(user, 'is_staff', False):
        return queryset
    return queryset.filter(**{field: user})


class OwnedQuerysetMixin:
    """Viewset mixin returning only the requesting user's own rows.

    Subclasses define ``owned_queryset`` (or override ``get_owned_queryset``)
    and, when ``staff_sees_all`` is true, staff users receive the full set.
    """

    owned_queryset = None
    owner_field = 'user'
    staff_sees_all = False

    def get_owned_queryset(self):
        if self.owned_queryset is None:
            raise NotImplementedError('Set owned_queryset or override get_owned_queryset().')
        return self.owned_queryset.all()

    def get_queryset(self):
        queryset = self.get_owned_queryset()
        if self.staff_sees_all:
            return scope_to_user(queryset, self.request.user, self.owner_field)
        return queryset.filter(**{self.owner_field: self.request.user})

    def perform_create(self, serializer):
        serializer.save(**{self.owner_field: self.request.user})
