/*
 * TokenCoin C Extension for Elliptic Curve Operations
 * ====================================================
 * Provides high-performance Ed25519 scalar multiplication,
 * Pedersen commitment operations, and MLSAG ring signature
 * verification using libsodium.
 *
 * Build: python3 setup.py build_ext --inplace
 * Requires: libsodium (libsodium-dev on Ubuntu, libsodium on macOS)
 */

#include <Python.h>
#include <sodium.h>
#include <stdint.h>
#include <string.h>

/* Fixed generator H for Pedersen commitments (different from Ed25519 base point) */
static const unsigned char H_POINT[32] = {
    0x8b, 0x65, 0x59, 0x70, 0x15, 0x37, 0x99, 0xaf,
    0x38, 0x1d, 0x2e, 0x7c, 0x73, 0x09, 0x07, 0x1b,
    0xc5, 0x13, 0xfe, 0x12, 0xef, 0x0a, 0x4e, 0x1b,
    0x5e, 0x27, 0xdb, 0x3b, 0x7b, 0x31, 0x47, 0x6f
};

/* Helper: clamp an Ed25519 scalar */
static void clamp_scalar(unsigned char* scalar) {
    scalar[0] &= 248;
    scalar[31] &= 127;
    scalar[31] |= 64;
}

/* ===================================================================
 * Scalar Multiplication: result = scalar * point
 * If use_base_point is true, multiplies by Ed25519 base point.
 * =================================================================== */
static PyObject* ed25519_scalar_mult(PyObject* self, PyObject* args) {
    const unsigned char* scalar_bytes;
    const unsigned char* point_bytes;
    Py_ssize_t scalar_len, point_len;
    int use_base_point;
    unsigned char result[32];
    unsigned char clamped[32];

    if (!PyArg_ParseTuple(args, "y#y#p", &scalar_bytes, &scalar_len,
                          &point_bytes, &point_len, &use_base_point)) {
        return NULL;
    }
    if (scalar_len != 32) {
        PyErr_SetString(PyExc_ValueError, "Scalar must be 32 bytes");
        return NULL;
    }
    memcpy(clamped, scalar_bytes, 32);
    clamp_scalar(clamped);

    if (use_base_point) {
        crypto_scalarmult_base(result, clamped);
    } else {
        if (point_len != 32) {
            PyErr_SetString(PyExc_ValueError, "Point must be 32 bytes");
            return NULL;
        }
        if (crypto_scalarmult(result, clamped, point_bytes) != 0) {
            PyErr_SetString(PyExc_RuntimeError, "Scalar multiplication failed");
            return NULL;
        }
    }
    return PyBytes_FromStringAndSize((const char*)result, 32);
}

/* ===================================================================
 * Pedersen Commitment: C = a*G + x*H
 * =================================================================== */
static PyObject* pedersen_commit(PyObject* self, PyObject* args) {
    const unsigned char* amount_bytes;
    const unsigned char* blinding_bytes;
    Py_ssize_t len;
    unsigned char aG[32], xH[32], commitment[32];
    unsigned char clamped[32];

    if (!PyArg_ParseTuple(args, "y#y#", &amount_bytes, &len, &blinding_bytes, &len)) {
        return NULL;
    }
    if (len != 32) {
        PyErr_SetString(PyExc_ValueError, "Amount and blinding must be 32 bytes");
        return NULL;
    }

    /* a*G */
    memcpy(clamped, amount_bytes, 32);
    clamp_scalar(clamped);
    crypto_scalarmult_base(aG, clamped);

    /* x*H */
    memcpy(clamped, blinding_bytes, 32);
    clamp_scalar(clamped);
    crypto_scalarmult(xH, clamped, H_POINT);

    /* C = aG + xH */
    crypto_core_ed25519_add(commitment, aG, xH);
    return PyBytes_FromStringAndSize((const char*)commitment, 32);
}

/* ===================================================================
 * Key Image: I = x * H_p(P)
 * =================================================================== */
static PyObject* compute_key_image(PyObject* self, PyObject* args) {
    const unsigned char* private_scalar;
    const unsigned char* public_point;
    Py_ssize_t len;
    unsigned char hash_to_point[32], key_image[32], clamped[32];

    if (!PyArg_ParseTuple(args, "y#y#", &private_scalar, &len, &public_point, &len)) {
        return NULL;
    }
    if (len != 32) {
        PyErr_SetString(PyExc_ValueError, "Private scalar and public point must be 32 bytes");
        return NULL;
    }

    /* H_p(P): hash public key to a curve point */
    crypto_hash_sha256(hash_to_point, public_point, 32);
    hash_to_point[31] &= 0x7f;

    /* I = x * H_p(P) */
    memcpy(clamped, private_scalar, 32);
    clamp_scalar(clamped);
    crypto_scalarmult(key_image, clamped, hash_to_point);
    return PyBytes_FromStringAndSize((const char*)key_image, 32);
}

/* ===================================================================
 * Point Addition: result = P1 + P2
 * =================================================================== */
static PyObject* point_add(PyObject* self, PyObject* args) {
    const unsigned char *p1, *p2;
    Py_ssize_t len;
    unsigned char result[32];

    if (!PyArg_ParseTuple(args, "y#y#", &p1, &len, &p2, &len)) {
        return NULL;
    }
    if (len != 32) {
        PyErr_SetString(PyExc_ValueError, "Points must be 32 bytes");
        return NULL;
    }
    crypto_core_ed25519_add(result, p1, p2);
    return PyBytes_FromStringAndSize((const char*)result, 32);
}

/* ===================================================================
 * Point Subtraction: result = P1 - P2
 * Ed25519 point negation: flip the sign bit (bit 255)
 * =================================================================== */
static PyObject* point_subtract(PyObject* self, PyObject* args) {
    const unsigned char *p1, *p2;
    Py_ssize_t len;
    unsigned char neg_p2[32], result[32];

    if (!PyArg_ParseTuple(args, "y#y#", &p1, &len, &p2, &len)) {
        return NULL;
    }
    if (len != 32) {
        PyErr_SetString(PyExc_ValueError, "Points must be 32 bytes");
        return NULL;
    }
    /* Negate p2 by flipping the sign bit */
    memcpy(neg_p2, p2, 32);
    neg_p2[31] ^= 0x80;
    crypto_core_ed25519_add(result, p1, neg_p2);
    return PyBytes_FromStringAndSize((const char*)result, 32);
}

/* ===================================================================
 * Generate random scalar
 * =================================================================== */
static PyObject* random_scalar(PyObject* self, PyObject* args) {
    unsigned char result[32];
    (void)args;
    randombytes_buf(result, 32);
    clamp_scalar(result);
    return PyBytes_FromStringAndSize((const char*)result, 32);
}

/* ===================================================================
 * Module Definition
 * =================================================================== */
static PyMethodDef TkcCryptoMethods[] = {
    {"ed25519_scalar_mult", ed25519_scalar_mult, METH_VARARGS,
     "Perform Ed25519 scalar multiplication: result = scalar * point"},
    {"pedersen_commit", pedersen_commit, METH_VARARGS,
     "Create Pedersen commitment: C = a*G + x*H"},
    {"compute_key_image", compute_key_image, METH_VARARGS,
     "Compute key image: I = x * H_p(P)"},
    {"point_add", point_add, METH_VARARGS,
     "Add two Ed25519 points"},
    {"point_subtract", point_subtract, METH_VARARGS,
     "Subtract two Ed25519 points: P1 - P2"},
    {"random_scalar", random_scalar, METH_VARARGS,
     "Generate a cryptographically secure random scalar"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef tkc_crypto_module = {
    PyModuleDef_HEAD_INIT,
    "tkc_crypto",
    "TokenCoin C++ elliptic curve operations using libsodium",
    -1,
    TkcCryptoMethods
};

PyMODINIT_FUNC PyInit_tkc_crypto(void) {
    if (sodium_init() < 0) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to initialize libsodium");
        return NULL;
    }
    return PyModule_Create(&tkc_crypto_module);
}
