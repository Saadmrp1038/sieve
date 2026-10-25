package sieve

// Hooks use the object's methods rather than importing a Kubernetes SDK. This
// keeps adding instrumentation from upgrading the test subject's dependencies.
import "reflect"

type objectMetadata interface {
	GetName() string
	GetNamespace() string
}

type ownerReference struct {
	Kind string
	Name string
}

func metadataForObject(object interface{}) (objectMetadata, bool) {
	value := reflect.ValueOf(object)
	if !value.IsValid() || (value.Kind() == reflect.Ptr && value.IsNil()) {
		return nil, false
	}
	if metadata, ok := object.(objectMetadata); ok {
		return metadata, true
	}
	method := value.MethodByName("GetObjectMeta")
	if method.IsValid() && method.Type().NumIn() == 0 && method.Type().NumOut() == 1 {
		return metadataForObject(method.Call(nil)[0].Interface())
	}
	return nil, false
}

func ownerReferencesForObject(object interface{}) []ownerReference {
	metadata, ok := metadataForObject(object)
	if !ok {
		return nil
	}
	value := reflect.ValueOf(metadata)
	if !value.IsValid() || (value.Kind() == reflect.Ptr && value.IsNil()) {
		return nil
	}
	method := value.MethodByName("GetOwnerReferences")
	if !method.IsValid() || method.Type().NumIn() != 0 || method.Type().NumOut() != 1 {
		return nil
	}
	owners := method.Call(nil)[0]
	if owners.Kind() != reflect.Slice {
		return nil
	}
	result := make([]ownerReference, 0, owners.Len())
	for i := 0; i < owners.Len(); i++ {
		owner := owners.Index(i)
		if owner.Kind() == reflect.Struct {
			kind, name := owner.FieldByName("Kind"), owner.FieldByName("Name")
			if kind.IsValid() && name.IsValid() && kind.Kind() == reflect.String && name.Kind() == reflect.String {
				result = append(result, ownerReference{Kind: kind.String(), Name: name.String()})
			}
		}
	}
	return result
}

func reasonForError(err error) string {
	for err != nil {
		method := reflect.ValueOf(err).MethodByName("Status")
		if method.IsValid() && method.Type().NumIn() == 0 && method.Type().NumOut() == 1 {
			status := method.Call(nil)[0]
			if status.Kind() == reflect.Struct {
				reason := status.FieldByName("Reason")
				if reason.IsValid() && reason.Kind() == reflect.String {
					return reason.String()
				}
			}
		}
		wrapped, ok := err.(interface{ Unwrap() error })
		if !ok {
			break
		}
		err = wrapped.Unwrap()
	}
	return "Unknown"
}
