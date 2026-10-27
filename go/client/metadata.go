package sieve

// Hooks use the object's methods rather than importing a Kubernetes SDK. This
// keeps adding instrumentation from upgrading the test subject's dependencies.
import "reflect"

// Metadata and full-object informers have independent caches even when they
// watch the same Kubernetes resource. Keep their receipts in separate streams.
func informerObserver(object interface{}) string {
	typeOf := reflect.TypeOf(object)
	if typeOf != nil && typeOf.Kind() == reflect.Ptr {
		typeOf = typeOf.Elem()
	}
	if typeOf != nil && typeOf.PkgPath() == "k8s.io/apimachinery/pkg/apis/meta/v1" && typeOf.Name() == "PartialObjectMetadata" {
		return "metadata-informer"
	}
	return "informer"
}

// Metadata-only watches use one Go type for many Kubernetes kinds.
func runtimeObjectKind(value reflect.Value) string {
	method := value.MethodByName("GetObjectKind")
	if !method.IsValid() || method.Type().NumIn() != 0 || method.Type().NumOut() != 1 {
		return ""
	}
	objectKind := method.Call(nil)[0]
	if objectKind.Kind() == reflect.Interface && objectKind.IsNil() {
		return ""
	}
	gvk := objectKind.MethodByName("GroupVersionKind")
	if !gvk.IsValid() || gvk.Type().NumIn() != 0 || gvk.Type().NumOut() != 1 {
		return ""
	}
	result := gvk.Call(nil)[0]
	if result.Kind() != reflect.Struct {
		return ""
	}
	kind := result.FieldByName("Kind")
	if kind.IsValid() && kind.Kind() == reflect.String {
		return kind.String()
	}
	return ""
}

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
